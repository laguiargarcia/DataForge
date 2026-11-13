"""Session lifecycle hooks (df-oahj, df-abrb): identity, the session bead, and the reaper.

    lifecycle.py start        SessionStart: register, export identity, print a short context
    lifecycle.py end          SessionEnd: hand the record to a detached closer, return at once
    lifecycle.py agent-start  SubagentStart / SubagentStop: mark a subagent running or done,
    lifecycle.py agent-stop   so its worktree claims stop counting once it is finished
    lifecycle.py cwd          CwdChanged: the session moved into a worktree nobody alive holds
    lifecycle.py bg-start     (detached) create or reopen the beads of sessions that started, reap
    lifecycle.py bg-end       (detached) write the closing record, close the beads of ended ones

The hook input arrives as JSON on stdin. Everything synchronous touches files only; every bd call
runs in a detached process, because a bd write costs 1-4 s on /mnt/c and SessionEnd hooks get
1.5 s. The session bead (type `session`, status `live` while running) is an index written only here:
start facts, then a structured end record. Work knowledge goes on the issue bead, not here.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import state

BD_TIMEOUT = 120
GIT_TIMEOUT = 20
GH_TIMEOUT = 20
LOG_LIMIT = 256 * 1024
BRIEF_FILES = 3
ENV_KEYS = ("BEADS_ACTOR", "DF_SESSION_BEAD", "DF_SESSION_ID", "CLAUDE_SESSION_ID")
BG_JOBS = {"bg-start": "bg-start", "bg-end": "bg-end"}  # fixed strings only reach the command line

try:
    import fcntl
except ImportError:  # Windows: no lock, and nothing is reaped there anyway
    fcntl = None


# --------------------------------------------------------------------------- bd and git


class Bd:
    """The `bd` CLI, run in the main checkout as a given actor."""

    def __init__(self, cwd: Path, actor: str | None = None, session_id: str | None = None,
                 exe: str = "bd"):
        self.cwd, self.actor, self.session_id, self.exe = cwd, actor, session_id, exe

    def run(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if self.actor:
            env["BEADS_ACTOR"] = self.actor
        if self.session_id:
            env["CLAUDE_SESSION_ID"] = self.session_id
        return subprocess.run([self.exe, *args], cwd=self.cwd, env=env, capture_output=True,
                              text=True, encoding="utf-8", timeout=BD_TIMEOUT, check=False)

    def json(self, *args: str):
        r = self.run(*args, "--json")
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout or "null")
        except ValueError:
            return None

    def show(self, issue: str) -> dict | None:
        found = self.json("show", issue)
        return found[0] if isinstance(found, list) and found else None


def git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                           encoding="utf-8", timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def changed_files(worktree: Path) -> list[str]:
    out = git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    if not out:
        return []
    return [rec[3:] for rec in out.split("\0") if len(rec) > 3]  # noqa: PLR2004 -- "XY " prefix


def bead_prefix(repo: state.Repo, bd: Bd) -> str | None:
    """The bd issue prefix, cached in the registry dir after the first lookup."""
    cached = repo.bead_prefix()
    if cached:
        return cached
    try:
        r = bd.run("config", "get", "issue_prefix")
    except (OSError, subprocess.SubprocessError):
        return None
    prefix = (r.stdout or "").strip() if r.returncode == 0 else ""
    if prefix and " " not in prefix:
        repo.registry.mkdir(parents=True, exist_ok=True)
        (repo.registry / state.PREFIX_CACHE).write_text(prefix + "\n", encoding="utf-8")
        return prefix
    return None


# --------------------------------------------------------------------------- facts


def start_facts(entry: dict) -> dict:
    """What the session bead stores at start: enough to find, reach and judge the session."""
    keys = ("session_id", "actor", "host", "pid", "pid_start", "cwd", "started_at", "source",
            "transcript_path", "session_title", "messaging_socket")
    facts = {k: entry[k] for k in keys if entry.get(k) is not None}
    facts["worktrees"] = sorted(entry.get("worktrees", {}))
    return facts


def worktree_record(path: Path, started_at: str, base: str) -> dict:
    """Branch, the commits made on it since the session started, and its PR if `gh` knows it."""
    rec: dict = {"path": str(path)}
    branch = (git(path, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    rec["branch"] = branch or None
    log = git(path, "log", f"{base}..HEAD", f"--since={started_at}", "--format=%H%x09%s") or ""
    rec["commits"] = [line.split("\t", 1)[0] for line in log.splitlines() if line]
    rec["pr"] = pr_for(path, branch) if branch and branch != "HEAD" else None
    return rec


def pr_for(cwd: Path, branch: str) -> dict | None:
    try:
        r = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "1",
                            "--json", "number,url,state"], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", timeout=GH_TIMEOUT, check=False)
        found = json.loads(r.stdout) if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return found[0] if found else None


def beads_touched(bd: Bd, actor: str, started_at: str, own_bead: str | None) -> dict:
    """Issues this actor created, closed while assigned, or still holds (bd keeps created_by and
    assignee, not who closed an issue, so `closed` means closed while assigned to it)."""
    rows = bd.json("list", "--all", "-n", "0", "--updated-after", started_at) or []
    created = sorted(i["id"] for i in rows if i.get("created_by") == actor and i["id"] != own_bead)
    closed = sorted(i["id"] for i in rows if i.get("status") == "closed" and i.get("assignee") == actor
                    and (i.get("closed_at") or "")[:19] >= started_at[:19])
    held = sorted(i["id"] for i in rows if i.get("status") == "in_progress" and i.get("assignee") == actor)
    return {"created": created, "closed": closed, "held": held}


# --------------------------------------------------------------------------- session bead


def sync_start(bd: Bd, bead: str, entry: dict) -> None:
    """Create the session bead, or reopen it on resume. Never `create --id` an existing one:
    bd 1.0.3 then overwrites it silently (title, status, metadata)."""
    facts = json.dumps(start_facts(entry), sort_keys=True)
    existing = bd.show(bead)
    if existing is None:
        title = f"session {entry['actor']} @ {Path(entry.get('cwd') or '.').name}"
        r = bd.run("create", title, "-t", "session", "--id", bead, "-l", "session",
                   "--metadata", facts, "--silent")
        if r.returncode != 0:
            return
    else:
        merged = {**(existing.get("metadata") or {}), **json.loads(facts)}
        bd.run("update", bead, "--metadata", json.dumps(merged, sort_keys=True))
    bd.run("update", bead, "--status", "live")


def release_claims(bd: Bd, issues: list[str], why: str) -> list[str]:
    released = []
    for issue in issues:
        r = bd.run("update", issue, "--status", "open", "--assignee", "",
                   "--append-notes", why)
        if r.returncode == 0:
            released.append(issue)
    return released


def close_session(bd: Bd, ending: dict, base: str = "origin/main") -> None:
    """Write the structured end record on the session bead, release its claims, close it."""
    bead, actor = ending.get("bead"), ending.get("actor")
    if not bead or not actor:
        return
    started = ending.get("started_at") or state.now_iso()
    paths = [Path(p) for p in sorted(ending.get("worktrees", {}))]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda p: worktree_record(p, started, base), paths))
    touched = beads_touched(bd, actor, started, bead)
    reason = ending.get("end_reason") or "other"
    why = (f"Claim liberado automaticamente: a sessão {actor} terminou ({reason}) sem fechar este "
           f"issue. Worktrees: {', '.join(r['path'] for r in records) or 'nenhum'}.")
    released = release_claims(bd, touched["held"], why)
    existing = bd.show(bead) or {}
    meta = {**(existing.get("metadata") or {}), **start_facts(ending),
            "ended_at": ending.get("ended_at") or state.now_iso(), "end_reason": reason,
            "end": {"worktrees": records, "beads_created": touched["created"],
                    "beads_closed": touched["closed"], "claims_released": released}}
    if ending.get("reaped_by"):
        meta["reaped_by"] = ending["reaped_by"]
    bd.run("update", bead, "--metadata", json.dumps(meta, sort_keys=True))
    bd.run("close", bead, "-r", "abandoned (process gone)" if reason == "abandoned" else f"ended: {reason}")


@contextlib.contextmanager
def reap_lock(repo: state.Repo):
    """Non-blocking: if another session is reaping right now, skip (True means we hold it)."""
    if fcntl is None:
        yield True
        return
    repo.registry.mkdir(parents=True, exist_ok=True)
    with open(repo.registry / ".reap.lock", "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def reapable(repo: state.Repo, bead: dict, me: set[str], entries: dict[str, dict]) -> dict | None:
    """The closing record for a live session bead whose Claude process is gone, else None."""
    meta = bead.get("metadata") or {}
    sid = meta.get("session_id")
    if not sid or sid in me or meta.get("host") != state.host_key():
        return None
    if (repo.registry / state.ENDING_DIR / state.entry_path(repo, sid).name).exists():
        return None  # its own closer is on it
    if state.pid_alive(meta.get("pid"), meta.get("pid_start")) is not False:
        return None
    entry = entries.get(sid) or {}
    if entry and state.entry_alive(entry):
        return None  # resumed in a new process; its bg-start has not updated the bead yet
    worktrees = entry.get("worktrees") or {p: {} for p in meta.get("worktrees", [])}
    return {**meta, **entry, "worktrees": worktrees, "bead": bead["id"], "end_reason": "abandoned",
            "ended_at": state.now_iso()}


def reap(repo: state.Repo, bd: Bd, me: set[str]) -> list[str]:
    """Close the live session beads of this host whose Claude process is gone; returns their ids."""
    reaped = []
    with reap_lock(repo) as held:
        if not held:
            return reaped
        entries = state.load_entries(repo)
        for b in bd.json("list", "-t", "session", "-s", "live", "-n", "0") or []:
            dead = reapable(repo, b, me, entries)
            if dead is not None:
                close_session(bd, {**dead, "reaped_by": bd.actor})
                reaped.append(b["id"])
        state.drop_dead_entries(repo, entries)
    return reaped


# --------------------------------------------------------------------------- hooks


def live_worktrees(repo: state.Repo, me: str, entries: dict[str, dict]) -> dict[Path, dict]:
    """{worktree: session entry} for the worktrees other live sessions hold or started in."""
    rows: dict[Path, dict] = {}
    for sid, e in entries.items():
        if sid == me or not state.entry_alive(e):
            continue
        held = [Path(p) for p in e.get("worktrees", {})]
        if e.get("cwd"):
            held.append(repo.worktree_of(Path(e["cwd"])))
        for wt in held:
            if wt in repo.worktrees():
                rows.setdefault(wt, e)
    return rows


def brief_line(repo: state.Repo, wt: Path, entry: dict, changed: list[str]) -> str:
    prefix = repo.bead_prefix()
    branch = repo.head_of(wt)
    bead = state.bead_id_in(wt.name, prefix) or state.bead_id_in(branch, prefix) or "no bead"
    shown = ", ".join(changed[:BRIEF_FILES]) or "none"
    if len(changed) > BRIEF_FILES:
        shown += f" +{len(changed) - BRIEF_FILES}"
    where = (wt.relative_to(repo.main) if state.is_within(wt, repo.main) else wt).as_posix()
    return (f"- {where}: {bead}, branch {branch}, session {entry.get('actor')} "
            f"(pid {entry.get('pid')}); uncommitted: {shown}")


def brief(repo: state.Repo, me: str, entries: dict[str, dict]) -> str:
    """A few lines on the other live sessions' worktrees; empty when nobody else is live."""
    rows = live_worktrees(repo, me, entries)
    if not rows:
        return ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        files = dict(zip(rows, pool.map(changed_files, rows), strict=True))
    lines = ["Other live Claude sessions in this repo (from scripts/sessions/board.py):"]
    lines += [brief_line(repo, wt, rows[wt], files[wt]) for wt in sorted(rows, key=str)]
    lines.append("Work only in your own worktree. Owner of any path: python3 scripts/sessions/board.py --owner PATH")
    return "\n".join(lines)


def export_identity(env_file: str | None, entry: dict) -> None:
    if not env_file:
        return
    values = {"BEADS_ACTOR": entry["actor"], "DF_SESSION_BEAD": entry.get("bead") or "",
              "DF_SESSION_ID": entry["session_id"], "CLAUDE_SESSION_ID": entry["session_id"]}
    lines = [f"export {k}={shlex.quote(values[k])}\n" for k in ENV_KEYS if values[k]]
    with open(env_file, "a", encoding="utf-8") as f:
        f.writelines(lines)


def spawn(repo: state.Repo, job: str) -> None:
    """Run this script's `bg-start` or `bg-end` detached, so it outlives the hook (and the session,
    for bg-end). Nothing from the hook input reaches the command line: the job reads marker files."""
    if job not in BG_JOBS:
        return
    repo.registry.mkdir(parents=True, exist_ok=True)
    log = repo.registry / "hooks.log"
    with contextlib.suppress(OSError):
        if log.stat().st_size > LOG_LIMIT:
            log.unlink()
    with open(log, "a", encoding="utf-8") as out:
        subprocess.Popen([sys.executable, "-S", str(Path(__file__).resolve()), BG_JOBS[job]],
                         cwd=repo.main, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True, close_fds=True)


def on_start(repo: state.Repo, event: dict, env: dict, *, background: bool = True,
             bd_exe: str | None = "bd") -> str:
    sid = str(event.get("session_id") or "")
    if not sid:
        return ""
    entries = state.load_entries(repo)
    # Read once per repository, then cached: the session bead id is exported right away.
    prefix = repo.bead_prefix() or (bead_prefix(repo, Bd(repo.main, exe=bd_exe)) if bd_exe else None)
    entry = entries.get(sid) or state.new_entry(sid, prefix, None)
    pid = state.find_claude_pid()
    entry.update({
        "pid": pid, "pid_start": state.proc_start(pid) if pid else None, "host": state.host_key(),
        "cwd": event.get("cwd"), "transcript_path": event.get("transcript_path"),
        "source": event.get("source"), "session_title": event.get("session_title"),
        "messaging_socket": env.get("CLAUDE_CODE_MESSAGING_SOCKET") or None,
    })
    if prefix and not entry.get("bead"):
        entry["bead"] = state.session_bead_for(sid, prefix)
    cwd_wt = repo.worktree_of(Path(event["cwd"])) if event.get("cwd") else None
    if cwd_wt and not state.holders(cwd_wt, sid, None, entries):
        state.claim_worktree(entry, cwd_wt, None)
    state.write_json(state.entry_path(repo, sid), entry)
    entries[sid] = entry
    export_identity(env.get("CLAUDE_ENV_FILE"), entry)
    if event.get("source") != "compact":
        state.write_json(repo.registry / state.STARTING_DIR / state.entry_path(repo, sid).name, entry)
        if background:
            spawn(repo, "bg-start")
    return brief(repo, sid, entries)


def on_end(repo: state.Repo, event: dict, *, background: bool = True) -> None:
    sid = str(event.get("session_id") or "")
    path = state.entry_path(repo, sid)
    entry = state.read_json(path)
    if not sid or entry is None:
        return
    entry.update({"end_reason": event.get("reason") or "other", "ended_at": state.now_iso()})
    state.write_json(repo.registry / state.ENDING_DIR / path.name, entry)
    path.unlink()
    if background:
        spawn(repo, "bg-end")


def on_agent(repo: state.Repo, event: dict, live: bool) -> None:
    sid, agent = str(event.get("session_id") or ""), event.get("agent_id")
    path = state.entry_path(repo, sid)
    entry = state.read_json(path)
    if entry is None or not agent:
        return
    entry.setdefault("agents", {})[agent] = {"live": live, "type": event.get("agent_type"), "at": state.now_iso()}
    state.write_json(path, entry)


def on_cwd(repo: state.Repo, event: dict) -> None:
    """The session `cd`-ed somewhere: if into a worktree nobody alive holds, it is now this one's."""
    sid = str(event.get("session_id") or "")
    new = event.get("new_cwd") or event.get("cwd")
    wt = repo.worktree_of(Path(new)) if new else None
    entries = state.load_entries(repo)
    entry = entries.get(sid)
    if wt is None or entry is None or state.holders(wt, sid, None, entries):
        return
    if state.claim_worktree(entry, wt, event.get("agent_id")):
        state.write_json(state.entry_path(repo, sid), entry)


def take_markers(repo: state.Repo, kind: str) -> list[tuple[Path, dict]]:
    """Claim the pending marker files of one kind (renamed to `<sid>.<pid>.work`, so two jobs never
    take the same one). A marker held by a job that died goes back to the queue first."""
    folder = repo.registry / kind
    if not folder.is_dir():
        return []
    for stale in folder.glob("*.work"):
        stem, owner, _ = stale.name.rsplit(".", 2)
        if owner.isdigit() and state.pid_alive(int(owner)) is False:
            with contextlib.suppress(OSError):
                os.replace(stale, folder / f"{stem}.json")
    taken = []
    for marker in sorted(folder.glob("*.json")):
        mine = folder / f"{marker.stem}.{os.getpid()}.work"
        try:
            os.replace(marker, mine)
        except OSError:
            continue
        data = state.read_json(mine)
        if data and data.get("session_id"):
            taken.append((mine, data))
        else:
            mine.unlink()
    return taken


def bg_start(repo: state.Repo, exe: str = "bd") -> None:
    """Create or reopen the bead of every session that started, then reap dead ones."""
    done: set[str] = set()
    bd = Bd(repo.main, exe=exe)
    for marker, started in take_markers(repo, state.STARTING_DIR):
        sid = started["session_id"]
        entry = state.read_json(state.entry_path(repo, sid)) or started  # the registry is fresher
        bd = Bd(repo.main, entry["actor"], sid, exe)
        prefix = bead_prefix(repo, bd)
        if prefix:
            sync_start(bd, entry.get("bead") or state.session_bead_for(sid, prefix), entry)
        marker.unlink()
        done.add(sid)
    reap(repo, bd, done)


def bg_end(repo: state.Repo, exe: str = "bd") -> None:
    """Write the end record and close the bead of every session that ended."""
    for marker, ending in take_markers(repo, state.ENDING_DIR):
        sid = ending["session_id"]
        bd = Bd(repo.main, ending.get("actor"), sid, exe)
        if not ending.get("bead"):
            prefix = bead_prefix(repo, bd)
            ending["bead"] = state.session_bead_for(sid, prefix) if prefix else None
        close_session(bd, ending)
        marker.unlink()


def run_hook(cmd: str, event: dict) -> str:
    """One hook call; returns what goes to stdout (context, for SessionStart only)."""
    repo = state.Repo(Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."))
    if cmd == "start":
        return on_start(repo, event, dict(os.environ))
    if cmd == "end":
        on_end(repo, event)
    elif cmd in {"agent-start", "agent-stop"}:
        on_agent(repo, event, live=cmd == "agent-start")
    elif cmd == "cwd":
        on_cwd(repo, event)
    return ""


def main(argv: list[str]) -> None:
    """Never fails: every error is swallowed (logged for the detached jobs), exit status 0."""
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd in BG_JOBS.values():
            repo = state.Repo(Path(os.environ.get("CLAUDE_PROJECT_DIR") or "."))
            (bg_start if cmd == "bg-start" else bg_end)(repo)
        else:
            text = run_hook(cmd, json.loads(sys.stdin.read() or "{}"))
            if text:
                sys.stdout.write(text + "\n")
    except Exception as exc:  # noqa: BLE001 -- fail open: a hook bug must never stall a session
        if cmd in BG_JOBS.values():
            sys.stderr.write(f"{state.now_iso()} {cmd}: {exc!r}\n")


if __name__ == "__main__":
    main(sys.argv)
