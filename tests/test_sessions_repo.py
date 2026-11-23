"""tests/sessions_repo.py: the throwaway repositories start no git process of their own (df-jsh4).

Git starts its maintenance detached after a commit, a fetch or a received push. It outlives the
command, and its lock file comes and goes under .git/objects while a test copies or removes the
tree: orders (1/2) of PR #170 failed in make_repo on a maintenance.lock that was gone.
"""
import os
import subprocess

from tests.sessions_repo import GIT_ENV, make_repo, write


def _trace(cwd, *args: str) -> str:
    """What git ran to do `args`, child processes included."""
    env = {**os.environ, **GIT_ENV, "GIT_TRACE": "1"}
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True)
    return r.stderr


def test_a_commit_a_push_and_a_fetch_start_no_background_maintenance(tmp_path):
    repo = make_repo(tmp_path)
    write(repo / "tracked.txt", "y\n")
    trace = "".join([
        _trace(repo, "commit", "-q", "-a", "-m", "second"),
        _trace(repo, "push", "-q", "origin", "main"),
        _trace(repo, "fetch", "-q", "origin"),
    ])
    assert "receive-pack" in trace  # the trace does reach the other repository's side of the push
    assert "maintenance" not in trace
    assert " gc " not in trace
