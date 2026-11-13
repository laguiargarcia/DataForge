"""Session lifecycle hooks (df-oahj, df-abrb): identity, the session bead, and the reaper.

    lifecycle.py start        SessionStart: register, export identity, print a short context
    lifecycle.py end          SessionEnd: hand the record to a detached closer, return at once
    lifecycle.py agent-start  SubagentStart / SubagentStop: mark a subagent running or done,
    lifecycle.py agent-stop   so its worktree claims stop counting once it is finished
    lifecycle.py cwd          CwdChanged: the session moved into a worktree nobody alive holds
    lifecycle.py bg-start ID  (detached) create or reopen the session bead, reap dead sessions
    lifecycle.py bg-end ID    (detached) write the closing record and close the session bead

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


def reap(repo: state.Repo, bd: Bd, me: str) -> list[str]:
    """Close the live session beads of this host whose Claude process is gone; returns their ids."""
    reaped = []
    with reap_lock(repo) as held:
        if not held:
            return reaped
        entries = state.load_entries(repo)
        for b in bd.json("list", "-t", "session", "-s", "live", "-n", "0") or []:
            meta = b.get("metadata") or {}
            sid = meta.get("session_id")
            if not sid or sid == me or meta.get("host") != state.host_key():
                continue
            if (repo.registry / state.ENDING_DIR / f"{sid}.json").exists():
                continue  # its own closer is on it
            if state.pid_alive(meta.get("pid"), meta.get("pid_start")) is not False:
                continue
            entry = entries.get(sid) or {}
            if entry and state.entry_alive(entry):
                continue  # resumed in a new process; its bg-start has not updated the bead yet
            worktrees = entry.get("worktrees") or {p: {} for p in meta.get("worktrees", [])}
            dead = {**meta, **entry, "worktrees": worktrees, "bead": b["id"], "end_reason": "abandoned",
                    "ended_at": state.now_iso(), "reaped_by": bd.actor}
            close_session(bd, dead)
            reaped.append(b["id"])
        state.drop_dead_entries(repo, entries)
    return reaped


# --------------------------------------------------------------------------- hooks


def brief(repo: state.Repo, me: str, entries: dict[str, dict]) -> str:
    """A few lines on the other live sessions' worktrees; empty when nobody else is live."""
    prefix = repo.bead_prefix()
    rows = {}
    for sid, e in entries.items():
        if sid == me or not state.entry_alive(e):
            continue
        held = [Path(p) for p in e.get("worktrees", {})]
        cwd_wt = repo.worktree_of(Path(e["cwd"])) if e.get("cwd") else None
        for wt in [*held, *([cwd_wt] if cwd_wt else [])]:
            if wt in repo.worktrees():
                rows.setdefault(wt, e)
    if not rows:
        return ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        files = dict(zip(rows, pool.map(changed_files, rows), strict=True))
    lines = ["Other live Claude sessions in this repo (from scripts/sessions/board.py):"]
    for wt, e in sorted(rows.items(), key=lambda kv: str(kv[0])):
        branch = repo.head_of(wt)
        bead = state.bead_id_in(wt.name, prefix) or state.bead_id_in(branch, prefix) or "no bead"
        changed = files[wt]
        shown = ", ".join(changed[:BRIEF_FILES]) + (f" +{len(changed) - BRIEF_FILES}" if len(changed) > BRIEF_FILES else "")
        where = (wt.relative_to(repo.main) if state.is_within(wt, repo.main) else wt).as_posix()
        lines.append(f"- {where}: {bead}, "
                     f"branch {branch}, session {e.get('actor')} (pid {e.get('pid')}); "
                     f"uncommitted: {shown or 'none'}")
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


def spawn(repo: state.Repo, *args: str) -> None:
    """Run this script detached, so it outlives the hook (and the session, for bg-end)."""
    repo.registry.mkdir(parents=True, exist_ok=True)
    log = repo.registry / "hooks.log"
    with contextlib.suppress(OSError):
        if log.stat().st_size > LOG_LIMIT:
            log.unlink()
    with open(log, "a", encoding="utf-8") as out:
        subprocess.Popen([sys.executable, "-S", str(Path(__file__).resolve()), *args],
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
    if background and event.get("source") != "compact":
        spawn(repo, "bg-start", sid)
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
        spawn(repo, "bg-end", sid)


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


def bg_start(repo: state.Repo, sid: str, exe: str = "bd") -> None:
    entry = state.read_json(state.entry_path(repo, sid))
    if entry is None:
        return
    bd = Bd(repo.main, entry["actor"], sid, exe)
    prefix = bead_prefix(repo, bd)
    if not prefix:
        return
    bead = entry.get("bead") or state.session_bead_for(sid, prefix)
    sync_start(bd, bead, entry)
    reap(repo, bd, sid)


def bg_end(repo: state.Repo, sid: str, exe: str = "bd") -> None:
    path = repo.registry / state.ENDING_DIR / state.entry_path(repo, sid).name
    ending = state.read_json(path)
    if ending is None:
        return
    bd = Bd(repo.main, ending.get("actor"), sid, exe)
    if not ending.get("bead"):
        prefix = bead_prefix(repo, bd)
        ending["bead"] = state.session_bead_for(sid, prefix) if prefix else None
    close_session(bd, ending)
    path.unlink()


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
        if cmd in {"bg-start", "bg-end"}:
            repo = state.Repo(project)
            (bg_start if cmd == "bg-start" else bg_end)(repo, argv[2])
            return 0
        event = json.loads(sys.stdin.read() or "{}")
        repo = state.Repo(Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."))
        if cmd == "start":
            text = on_start(repo, event, dict(os.environ))
            if text:
                sys.stdout.write(text + "\n")
        elif cmd == "end":
            on_end(repo, event)
        elif cmd in {"agent-start", "agent-stop"}:
            on_agent(repo, event, live=cmd == "agent-start")
        elif cmd == "cwd":
            on_cwd(repo, event)
    except Exception as exc:  # noqa: BLE001 -- fail open: a hook bug must never stall a session
        if cmd.startswith("bg-"):
            sys.stderr.write(f"{state.now_iso()} {cmd}: {exc!r}\n")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
