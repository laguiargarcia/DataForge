"""The Claude Code hook of the quality gate (scripts/quality/hook.sh).

The hook must hand each edited file to the gate of the checkout that holds it. The clone's gate
does not see files under .worktrees/, so sending a worktree file to it reports "ok" unchecked
(df-5wsy). The gate itself is replaced by a stub that records who ran and with which arguments.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "quality" / "hook.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("jq"), reason="the hook is a bash script that needs jq"
)

# Records "<checkout>|<arguments>" and fails when an argument names a file called bad*.py.
STUB_GATE = """\
import os, sys
from pathlib import Path
with open(os.environ["GATE_LOG"], "a") as log:
    log.write(f"{Path(__file__).resolve().parents[2]}|{' '.join(sys.argv[1:])}\\n")
sys.exit(1 if any(Path(a).name.startswith("bad") for a in sys.argv[1:]) else 0)
"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True
    )


def _venv(checkout: Path) -> None:
    (checkout / ".venv" / "bin").mkdir(parents=True)
    (checkout / ".venv" / "bin" / "python").symlink_to(sys.executable)


@pytest.fixture
def clone(tmp_path):
    """A clone with a gate and a venv, and a worktree of it under .worktrees/wt with its own venv."""
    root = tmp_path / "clone"
    (root / "scripts" / "quality").mkdir(parents=True)
    (root / "scripts" / "quality" / "gate.py").write_text(STUB_GATE, encoding="utf-8")
    (root / ".gitignore").write_text(".worktrees/\n.venv/\n.claude/\n", encoding="utf-8")
    (root / ".claude").mkdir()
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "worktree", "add", "-q", ".worktrees/wt", "-b", "wt")
    _venv(root)
    _venv(root / ".worktrees" / "wt")
    return root


def _hook(clone: Path, verb: str, tmp_path: Path, **payload) -> tuple[int, list[str]]:
    log = tmp_path / "gate.log"
    log.unlink(missing_ok=True)
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(clone), "GATE_LOG": str(log)}
    env["PATH"] = f"{Path(shutil.which('jq')).parent}:{Path(shutil.which('git')).parent}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(HOOK), verb], input=json.dumps({"session_id": "s1", **payload}), text=True, env=env, check=False
    )
    return result.returncode, log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _edit(clone: Path, tmp_path: Path, file: Path) -> tuple[int, list[str]]:
    file.write_text("x = 1\n", encoding="utf-8")
    return _hook(clone, "post", tmp_path, tool_input={"file_path": str(file)})


def test_a_worktree_file_goes_to_the_gate_of_its_worktree(clone, tmp_path):
    worktree = clone / ".worktrees" / "wt"
    code, runs = _edit(clone, tmp_path, worktree / "mod.py")
    assert code == 0
    assert runs == [f"{worktree}|check {worktree / 'mod.py'}"]


def test_a_clone_file_goes_to_the_gate_of_the_clone(clone, tmp_path):
    code, runs = _edit(clone, tmp_path, clone / "mod.py")
    assert code == 0
    assert runs == [f"{clone}|check {clone / 'mod.py'}"]


def test_a_finding_in_a_worktree_file_is_fed_back(clone, tmp_path):
    code, _ = _edit(clone, tmp_path, clone / ".worktrees" / "wt" / "bad.py")
    assert code == 2


def test_a_worktree_without_a_venv_uses_the_clone_venv_on_its_own_gate(clone, tmp_path):
    worktree = clone / ".worktrees" / "wt"
    shutil.rmtree(worktree / ".venv")
    _, runs = _edit(clone, tmp_path, worktree / "mod.py")
    assert runs == [f"{worktree}|check {worktree / 'mod.py'}"]


def test_a_file_outside_any_checkout_goes_to_the_clone(clone, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _, runs = _edit(clone, tmp_path, outside / "mod.py")
    assert runs == [f"{clone}|check {outside / 'mod.py'}"]


def test_a_file_that_is_not_python_runs_no_gate(clone, tmp_path):
    (clone / "notes.md").write_text("x\n", encoding="utf-8")
    code, runs = _hook(clone, "post", tmp_path, tool_input={"file_path": str(clone / "notes.md")})
    assert (code, runs) == (0, [])


def test_stop_runs_the_full_gate_once_per_checkout_with_its_own_files(clone, tmp_path):
    worktree = clone / ".worktrees" / "wt"
    for file in (clone / "a.py", worktree / "b.py", worktree / "c.py"):
        _edit(clone, tmp_path, file)
    code, runs = _hook(clone, "stop", tmp_path)
    assert code == 0
    assert sorted(runs) == sorted(
        [f"{clone}|check --full {clone / 'a.py'}", f"{worktree}|check --full {worktree / 'b.py'} {worktree / 'c.py'}"]
    )
    assert not (clone / ".claude" / ".quality-edited-s1").exists()


def test_stop_fails_when_any_checkout_fails_and_keeps_the_list_for_the_retry(clone, tmp_path):
    _edit(clone, tmp_path, clone / "a.py")
    _edit(clone, tmp_path, clone / ".worktrees" / "wt" / "bad.py")
    code, runs = _hook(clone, "stop", tmp_path)
    assert code == 2
    assert len(runs) == 2
    assert (clone / ".claude" / ".quality-edited-s1").exists()
    code, runs = _hook(clone, "stop", tmp_path, stop_hook_active=True)
    assert (code, runs) == (0, [])
    assert not (clone / ".claude" / ".quality-edited-s1").exists()


def test_stop_skips_files_that_no_longer_exist(clone, tmp_path):
    _edit(clone, tmp_path, clone / "a.py")
    (clone / "a.py").unlink()
    code, runs = _hook(clone, "stop", tmp_path)
    assert (code, runs) == (0, [])
    assert not (clone / ".claude" / ".quality-edited-s1").exists()
