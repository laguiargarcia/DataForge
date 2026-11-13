"""scripts/testloop/diffcover_cmd.py: which exit code each outcome of pytest and diff-cover gives (df-ahua.7).

pytest and diff-cover are replaced by canned results; tests/test_testloop.py runs the real ones.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

import diffcover_cmd  # the test loop tools are scripts, not an installed package
from common import Config, ToolError


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Replace subprocess.run: pytest writes the XML and exits `pytest`; diff-cover returns `diff`."""
    state = {"pytest": 0, "diff": (0, "", ""), "calls": []}

    def run(cmd, **kwargs):
        state["calls"].append((cmd, kwargs["env"]))
        if "pytest" in cmd:
            xml = next(a for a in cmd if a.startswith("--cov-report=xml:")).split(":", 1)[1]
            Path(xml).write_text("<coverage/>", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, state["pytest"], "1 failed", "")
        return subprocess.CompletedProcess(cmd, *state["diff"])

    monkeypatch.setattr(diffcover_cmd.subprocess, "run", run)
    state["config"] = Config(repo=tmp_path, python="py", pytest_args=["-p", "no:randomly"], env={"QT": "offscreen"})
    return state


def test_every_changed_line_covered_exits_0(fake):
    assert diffcover_cmd.main(fake["config"], "origin/main") == 0


def test_uncovered_changed_lines_exit_1_and_are_printed(fake, capsys):
    fake["diff"] = (1, "dataforge/x.py (50.0%): Missing lines 3\n", "Failure. Coverage is below 100%.\n")
    assert diffcover_cmd.main(fake["config"], "origin/main") == 1
    assert "Missing lines 3" in capsys.readouterr().out


def test_failing_tests_are_a_tool_error_and_diff_cover_never_runs(fake):
    fake["pytest"] = 1
    with pytest.raises(ToolError, match="engine tests failed"):
        diffcover_cmd.main(fake["config"], "origin/main")
    assert len(fake["calls"]) == 1


@pytest.mark.parametrize("diff", [(1, "", "fatal: bad revision 'nope'\n"), (2, "", "usage: diff-cover\n")])
def test_any_other_diff_cover_failure_is_a_tool_error(fake, diff):
    fake["diff"] = diff
    with pytest.raises(ToolError, match="diff-cover exited"):
        diffcover_cmd.main(fake["config"], "nope")


def test_commands_run_the_engine_tests_with_branch_coverage_and_judge_every_line(fake):
    diffcover_cmd.main(fake["config"], "main")
    (tests, env), (diff, _) = fake["calls"]
    assert tests[:5] == ["py", "-m", "pytest", "-p", "no:randomly"]
    assert {"tests", "--ignore-glob=tests/test_gui_*", "--cov=dataforge", "--cov-branch"} <= set(tests)
    assert tests[tests.index("-m", 3) + 1] == "not integration"
    assert diff[:3] == ["py", "-m", "diff_cover.diff_cover_tool"]
    assert {"--compare-branch=main", "--branch-coverage", "--fail-under=100"} <= set(diff)
    assert env["QT"] == "offscreen"
    assert Path(env["COVERAGE_FILE"]).parent == Path(diff[3]).parent  # the data stays in the temp dir
