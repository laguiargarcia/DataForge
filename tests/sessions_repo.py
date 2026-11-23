"""Throwaway git repositories and bd databases for the session-coordination tests (df-0dql).

Never the real repository or its .beads: everything lives under the test's tmp_path.
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


# Git's background maintenance, off in each repository's own config, so every copy carries it. A
# commit, a fetch or a received push starts it detached, and it creates and deletes .git/objects/maintenance.lock
# while make_repo copies the tree: the copy then fails on a file that is gone (df-jsh4).
NO_MAINTENANCE = {"maintenance.auto": "false", "receive.autogc": "false"}


def _build_repo(root: Path) -> None:
    """origin.git (bare) and repo (its clone on `main`, one commit pushed and fetched) under root."""
    origin = root / "origin.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    for key, value in NO_MAINTENANCE.items():
        git(origin, "config", key, value)
    main = root / "repo"
    git(root, "clone", "-q", *(f"--config={k}={v}" for k, v in NO_MAINTENANCE.items()), str(origin), str(main))
    git(main, "checkout", "-q", "-b", "main")
    write(main / ".gitignore", "ignored/\n.worktrees/\n.beads/\n.claude/\nAGENTS.md\nCLAUDE.md\n")
    write(main / "tracked.txt", "x\n")
    git(main, "add", ".")
    git(main, "commit", "-q", "-m", "first")
    git(main, "push", "-q", "origin", "main")
    git(main, "fetch", "-q", "origin")


_TEMPLATE: Path | None = None


def _template() -> Path:
    """The pair built once per test process: eight git calls each on Windows cost seconds (df-n8ac.4)."""
    global _TEMPLATE  # noqa: PLW0603 -- one lazily built template per process
    if _TEMPLATE is None:
        root = Path(tempfile.mkdtemp(prefix="sessions-repo-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _build_repo(root)
        _TEMPLATE = root
    return _TEMPLATE


def make_repo(tmp_path: Path) -> Path:
    """A main checkout on `main`, cloned from a bare origin, with origin/main fetched: a copy of the
    process's template, its origin pointed at its own copy of the bare repository."""
    template = _template()
    shutil.copytree(template / "origin.git", tmp_path / "origin.git", symlinks=True)
    main = tmp_path / "repo"
    shutil.copytree(template / "repo", main, symlinks=True)
    git(main, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
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
