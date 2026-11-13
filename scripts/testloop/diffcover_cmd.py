"""`diff-cover`: changed lines that no engine test runs, before a PR (df-ahua.7).

Runs the engine tests as CI's tests job does, with branch coverage of dataforge/, then diff-cover
on the change against a branch (default origin/main; committed, staged and unstaged edits).
Exit 0 every changed line and branch is covered, 1 some are not (listed), 2 the tests failed or a
tool broke. SonarQube Cloud judges the same on the PR; this is the local, earlier answer.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from common import Config, ToolError

ENGINE_TESTS = ["tests", "-m", "not integration", "--ignore-glob=tests/test_gui_*", "-q"]
BELOW = "Coverage is below"  # diff-cover's message when --fail-under is not met; any other exit 1 is an error


def _run(cmd: list[str], config: Config, env: dict[str, str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=config.repo, env=env, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ToolError(f"cannot run {cmd[0]}: {exc}") from exc


def main(config: Config, compare_branch: str) -> int:
    with tempfile.TemporaryDirectory(prefix="testloop-") as tmp:
        xml = Path(tmp) / "coverage.xml"
        # The coverage data stays in tmp, so a .coverage the developer keeps is not overwritten.
        env = {**os.environ, **config.env, "COVERAGE_FILE": str(Path(tmp) / ".coverage")}
        tests = _run([
            config.python, "-m", "pytest", *config.pytest_args, *ENGINE_TESTS,
            "--cov=dataforge", "--cov-branch", f"--cov-report=xml:{xml}",
        ], config, env)
        if tests.returncode != 0 or not xml.is_file():
            raise ToolError(f"engine tests failed (pytest exit {tests.returncode}):\n{(tests.stdout + tests.stderr)[-2000:]}")
        diff = _run([
            config.python, "-m", "diff_cover.diff_cover_tool", str(xml), f"--compare-branch={compare_branch}",
            "--branch-coverage", "--fail-under=100",
        ], config, env)
    print(diff.stdout, end="")
    print(diff.stderr, end="", file=sys.stderr)
    if diff.returncode == 0:
        return 0
    if diff.returncode == 1 and BELOW in diff.stderr:
        return 1
    raise ToolError(f"diff-cover exited {diff.returncode}")
