"""Shared state for the session hooks and the board (df-0dql): where things are, and who is alive.

Everything here is read from files, never from a git or bd process, so the PreToolUse guard can use
it on every tool call (the repo sits on /mnt/c today, where each git process costs about 0.5 s).

- The repository layout comes from the `.git` files: the common git dir, the main checkout, and each
  linked worktree from `<common>/worktrees/<name>/gitdir` and `HEAD`.
- The registry is one JSON file per Claude Code session under `<common>/df-sessions/`. It holds only
  what neither git nor bd can answer cheaply: the session's Claude process (PID and start time, to
  tell it alive), its actor and session bead, and the worktrees it works in. It is a cache for the
  hot path, not history: the session bead in bd is the record, and a dead session's file is dropped.

Standard library only: the hooks run under `python3 -S` (no site-packages, fast start).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_DIR = "df-sessions"
ENDING_DIR = "ending"
PREFIX_CACHE = "bead-prefix"
CLAUDE_NAMES = frozenset({"claude", "claude.exe"})
# Without a cached bd prefix, a bead id is the first `<letters>-<id>` in a worktree or branch name
# (the convention is `.worktrees/<issue>`).
GENERIC_BEAD = r"[a-z][a-z0-9]*"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def host_key() -> str:
    """This machine and OS. WSL and native Windows share a hostname but not a PID space."""
    return f"{socket.gethostname()}/{sys.platform}"


def actor_for(session_id: str) -> str:
    return f"claude-{session_id[:8]}"


def session_bead_for(session_id: str, prefix: str) -> str:
    return f"{prefix}-sess-{session_id[:8]}"


# --------------------------------------------------------------------------- repository layout


def find_dotgit(start: Path) -> Path | None:
    """The nearest `.git` (directory or file) at or above `start`."""
    for d in (start, *start.parents):
        g = d / ".git"
        if g.exists():
            return g
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def gitdir_of(dotgit: Path) -> Path:
    """The git dir a `.git` entry stands for: itself, or the target of a `gitdir:` file."""
    if dotgit.is_dir():
        return dotgit
    target = _read(dotgit).removeprefix("gitdir:").strip()
    p = Path(target)
    return p if p.is_absolute() else (dotgit.parent / p).resolve()


def common_dir_of(gitdir: Path) -> Path:
    cd = gitdir / "commondir"
    if cd.is_file():
        p = Path(_read(cd))
        return p if p.is_absolute() else (gitdir / p).resolve()
    return gitdir


class Repo:
    """The repository a project directory belongs to: common dir, main checkout, linked worktrees."""

    def __init__(self, project: Path):
        dotgit = find_dotgit(project.resolve())
        if dotgit is None:
            raise FileNotFoundError(f"no git repository at or above {project}")
        self.common = common_dir_of(gitdir_of(dotgit))
        self.main = self.common.parent.resolve()
        self.registry = self.common / REGISTRY_DIR
        self._worktrees: dict[Path, str] | None = None

    def worktrees(self) -> dict[Path, str]:
        """Linked worktrees: {worktree root: admin name}. The main checkout is not in it."""
        if self._worktrees is None:
            found = {}
            admin = self.common / "worktrees"
            for entry in sorted(admin.iterdir()) if admin.is_dir() else []:
                with contextlib.suppress(OSError):
                    root = Path(_read(entry / "gitdir")).parent.resolve()
                    found[root] = entry.name
            self._worktrees = found
        return self._worktrees

    def worktree_of(self, path: Path) -> Path | None:
        """The linked worktree holding `path`, or None (main checkout or outside the repo)."""
        best = None
        for root in self.worktrees():
            if is_within(path, root) and (best is None or len(root.parts) > len(best.parts)):
                best = root
        return best

    def head_of(self, worktree: Path | None) -> str:
        """Branch name (or detached commit) of a worktree, read from its HEAD file."""
        if worktree is None:
            head = self.common / "HEAD"
        else:
            head = self.common / "worktrees" / self.worktrees()[worktree] / "HEAD"
        try:
            text = _read(head)
        except OSError:
            return ""
        return text.removeprefix("ref: refs/heads/") if text.startswith("ref:") else text[:12]

    def nested_repo_of(self, path: Path) -> Path | None:
        """A separate git checkout inside the main checkout that holds `path` (e.g. a private
        workspace that is its own repository), or None."""
        for d in (path, *path.parents):
            if d == self.main or not is_within(d, self.main):
                return None
            if (d / ".git").exists():
                return d
        return None

    def bead_prefix(self) -> str | None:
        try:
            return _read(self.registry / PREFIX_CACHE) or None
        except OSError:
            return None


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def bead_id_in(text: str, prefix: str | None) -> str | None:
    """The issue id a worktree or branch name carries: `df-0dql` in `feat/df-0dql-session-x`,
    `df-ahua.5` in `df-ahua.5-ledger`."""
    head = re.escape(prefix) if prefix else GENERIC_BEAD
    m = re.search(rf"(?<![a-z0-9]){head}-[a-z0-9]{{3,}}(?:\.\d+)*", text)
    return m.group(0) if m else None


# --------------------------------------------------------------------------- processes


def proc_start(pid: int) -> str | None:
    """Start time of a process (clock ticks since boot, from /proc), or None if unknown."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # Field 2 (comm) may hold spaces and parentheses; fields after the last ')' are plain.
    fields = stat.rsplit(")", 1)[1].split()
    return fields[19] if len(fields) > 19 else None  # noqa: PLR2004 -- field 22 of proc(5)


def pid_alive(pid: int | None, start: str | None = None) -> bool | None:
    """True/False when known; None when this platform cannot tell (then nobody reaps it).

    With /proc the start time must match too, so a recycled PID does not keep a dead session alive.
    """
    if not pid:
        return None
    if Path("/proc/self/stat").exists():
        now = proc_start(pid)
        return now is not None and (start is None or now == start)
    if sys.platform == "win32":
        return None  # os.kill(pid, 0) would terminate the process on Windows
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parent_and_comm(pid: int) -> tuple[int, str] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    comm = stat[stat.find("(") + 1 : stat.rfind(")")]
    return int(stat.rsplit(")", 1)[1].split()[1]), comm


def find_claude_pid(start: int | None = None, names: frozenset[str] = CLAUDE_NAMES,
                    env: dict | None = None) -> int | None:
    """The Claude Code process this hook runs under.

    $CLAUDE_PID first (Claude Code sets it for hook commands and tool shells since v2.1.214); else
    the nearest ancestor named `claude`; None when neither is known.
    """
    value = (os.environ if env is None else env).get("CLAUDE_PID", "")
    if value.isdigit():
        return int(value)
    pid = start if start is not None else os.getppid()
    for _ in range(12):
        found = _parent_and_comm(pid)
        if found is None:
            return None
        parent, comm = found
        if comm in names:
            return pid
        if parent <= 1:
            return None
        pid = parent
    return None


# --------------------------------------------------------------------------- registry


def write_json(path: Path, data: dict) -> None:
    """Atomic write: readers see the old file or the new one, never half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def entry_path(repo: Repo, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return repo.registry / f"{safe}.json"


def load_entries(repo: Repo) -> dict[str, dict]:
    """Every registered session: {session_id: entry}."""
    out = {}
    for p in sorted(repo.registry.glob("*.json")) if repo.registry.is_dir() else []:
        e = read_json(p)
        if e and e.get("session_id"):
            out[e["session_id"]] = e
    return out


def entry_alive(entry: dict) -> bool:
    """A session counts as alive unless it is known dead: same host, and its process is gone.

    A session on another host (or a platform without /proc) is never presumed dead.
    """
    if entry.get("host") != host_key():
        return True
    return pid_alive(entry.get("pid"), entry.get("pid_start")) is not False


def new_entry(session_id: str, prefix: str | None, pid: int | None) -> dict:
    return {
        "session_id": session_id,
        "actor": actor_for(session_id),
        "bead": session_bead_for(session_id, prefix) if prefix else None,
        "host": host_key(),
        "pid": pid,
        "pid_start": proc_start(pid) if pid else None,
        "started_at": now_iso(),
        "worktrees": {},
        "agents": {},
    }


def claim_worktree(entry: dict, worktree: Path, agent_id: str | None) -> bool:
    """Record that this session (or one of its subagents) works in `worktree`. True if new."""
    key = str(worktree)
    held = entry.setdefault("worktrees", {}).get(key)
    if held is not None and (held.get("agent") or None) == (agent_id or None):
        return False
    if held is not None and not agent_id:
        return False  # the session itself already holds it through one of its agents
    entry["worktrees"][key] = {"agent": agent_id or "", "at": now_iso()}
    return True


def agent_live(entry: dict, agent_id: str) -> bool:
    return bool(entry.get("agents", {}).get(agent_id, {}).get("live"))


def holders(worktree: Path, me_session: str, me_agent: str | None, entries: dict[str, dict]) -> list[dict]:
    """The live sessions or sibling subagents other than me that hold `worktree`.

    - another live session holding it (any of its agents): foreign;
    - within my own session, a subagent other than me that holds it and is still running: foreign
      to me only if I am a subagent too (the parent session may use its subagents' worktrees).
    """
    key = str(worktree)
    found = []
    for sid, e in entries.items():
        held = e.get("worktrees", {}).get(key)
        started_inside = e.get("cwd") and is_within(Path(e["cwd"]), worktree)
        if not (held or started_inside):
            continue
        if sid != me_session:
            if entry_alive(e):
                found.append({"session_id": sid, "actor": e.get("actor"), "bead": e.get("bead"), "agent": None})
            continue
        agent = (held or {}).get("agent") or None
        if me_agent and agent and agent != me_agent and agent_live(e, agent):
            found.append({"session_id": sid, "actor": e.get("actor"), "bead": e.get("bead"), "agent": agent})
    return found


def drop_dead_entries(repo: Repo, entries: dict[str, dict]) -> list[dict]:
    """Remove the registry files of sessions known dead; returns those entries."""
    dead = [e for e in entries.values() if not entry_alive(e)]
    for e in dead:
        with contextlib.suppress(OSError):
            entry_path(repo, e["session_id"]).unlink()
    return dead

