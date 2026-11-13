"""Throwaway git repositories and bd databases for the session-coordination tests (df-0dql).

Never the real repository or its .beads: everything lives under the test's tmp_path.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "sessions"
sys.path.insert(0, str(SCRIPTS))

import state  # the session tools are scripts, not an installed package

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
HAS_BD = shutil.which("bd") is not None
OTHER_SESSION = "bbbbbbbb-2222-4000-8000-000000000002"
ME = "aaaaaaaa-1111-4000-8000-000000000001"


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True)
    return r.stdout


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def make_repo(tmp_path: Path) -> Path:
    """A main checkout on `main`, cloned from a bare origin, with origin/main fetched."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    main = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(main))
    git(main, "checkout", "-q", "-b", "main")
    write(main / ".gitignore", "ignored/\n.worktrees/\n.beads/\n.claude/\nAGENTS.md\nCLAUDE.md\n")
    write(main / "tracked.txt", "x\n")
    git(main, "add", ".")
    git(main, "commit", "-q", "-m", "first")
    git(main, "push", "-q", "origin", "main")
    git(main, "fetch", "-q", "origin")
    return main.resolve()


def add_worktree(main: Path, name: str) -> Path:
    git(main, "worktree", "add", "-q", f".worktrees/{name}", "-b", f"feat/{name}", "origin/main")
    return (main / ".worktrees" / name).resolve()


def register(main: Path, session_id: str, *, pid: int | None = None, worktrees=(), **fields) -> dict:
    """Write a registry entry as the SessionStart hook would. Default PID: this test process (alive).
    `fields` override entry keys (cwd, agents, bead...)."""
    repo = state.Repo(main)
    entry = state.new_entry(session_id, "df", os.getpid() if pid is None else pid)
    entry["worktrees"] = {str(w): {"agent": "", "at": state.now_iso()} for w in worktrees}
    entry.update({k: str(v) if isinstance(v, Path) else v for k, v in fields.items()})
    state.write_json(state.entry_path(repo, session_id), entry)
    return entry


def dead_pid() -> int:
    """The PID of a process that has exited."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def bd_init(main: Path) -> None:
    """A throwaway bd database in the temp repo, with the custom session type and status."""
    def bd(*args):
        subprocess.run(["bd", *args], cwd=main, capture_output=True, text=True, check=True)
    bd("init", "--prefix", "df", "-q", "--skip-hooks", "--skip-agents", "--non-interactive")
    bd("config", "set", "types.custom", "session")
    bd("config", "set", "status.custom", "live:wip")


def bd_json(main: Path, *args: str, actor: str | None = None):
    env = {**os.environ, **({"BEADS_ACTOR": actor} if actor else {})}
    r = subprocess.run(["bd", *args, "--json"], cwd=main, capture_output=True, text=True, env=env, check=True)
    return json.loads(r.stdout or "null")


def bd_run(main: Path, *args: str, actor: str | None = None) -> str:
    env = {**os.environ, **({"BEADS_ACTOR": actor} if actor else {})}
    r = subprocess.run(["bd", *args], cwd=main, capture_output=True, text=True, env=env, check=True)
    return r.stdout.strip()
