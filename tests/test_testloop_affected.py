"""scripts/testloop/affected.py: which mutation targets a PR's changed files can affect (df-gqke)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

import affected  # the test loop tools are scripts, not an installed package
from common import load_config

FILES = {
    "testloop.toml": (
        '[targets."dataforge/deploy/plan.py"]\ntests = ["tests/test_plan*.py"]\n\n'
        '[targets."dataforge/deploy/apply.py"]\ntests = ["tests/test_apply.py"]\n'
    ),
    "dataforge/__init__.py": "",
    "dataforge/models.py": "",
    "dataforge/gui.py": "import dataforge.models\n",
    "dataforge/deploy/__init__.py": "",
    "dataforge/deploy/plan.py": "from .ignore import load\n",
    "dataforge/deploy/ignore.py": "def load():\n    from ..models import X\n",
    "dataforge/deploy/apply.py": "from dataforge.deploy import plan\n",
    "dataforge/deploy/rollback.py": "",
    "dataforge/deploy/data.json": "{}",
    "tests/__init__.py": "",
    "tests/conftest.py": "",
    "tests/_helpers.py": "from dataforge.deploy import rollback\n",
    "tests/test_plan.py": "from dataforge.deploy.plan import load\n",
    "tests/test_apply.py": "import dataforge.deploy.apply\nfrom tests import _helpers\n",
    "docs/x.md": "",
}
PLAN, APPLY = "dataforge/deploy/plan.py", "dataforge/deploy/apply.py"


@pytest.fixture
def repo(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def picked(repo, changed):
    return affected.select(load_config(repo), changed)[0]


def test_dependencies_follow_relative_absolute_nested_and_package_imports(repo):
    deps = affected.dependencies(repo, [PLAN, "tests/test_plan.py"])
    assert deps == {
        PLAN, "tests/test_plan.py", "tests/__init__.py",
        "dataforge/__init__.py", "dataforge/deploy/__init__.py",
        "dataforge/deploy/ignore.py",   # from .ignore
        "dataforge/models.py",          # from ..models, inside a function
    }


@pytest.mark.parametrize("changed, expected", [
    ([PLAN], [PLAN, APPLY]),                          # apply imports plan
    (["dataforge/models.py"], [PLAN, APPLY]),         # two hops away, through ignore.py
    ([APPLY], [APPLY]),
    (["tests/test_plan.py"], [PLAN]),
    (["dataforge/deploy/rollback.py"], [APPLY]),      # reached only through a test helper
    (["dataforge/gui.py", "docs/x.md"], []),          # nothing a target imports
    ([], []),
])
def test_a_target_runs_when_its_module_tests_or_imports_change(repo, changed, expected):
    assert picked(repo, changed) == expected


@pytest.mark.parametrize("changed", [
    "testloop.toml", "pyproject.toml", "tests/conftest.py", ".github/workflows/ci.yml",
    "scripts/testloop/mutate_cmd.py",
    "dataforge/deploy/data.json",   # not Python: the import graph cannot see who reads it
    "dataforge/deleted.py",         # gone from HEAD: who imported it is unknown
])
def test_shared_or_invisible_changes_run_every_target(repo, changed):
    assert picked(repo, [changed]) == [PLAN, APPLY]


def test_cli_without_base_ref_prints_every_target(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert affected.main([]) == 0
    assert json.loads(capsys.readouterr().out) == [PLAN, APPLY]


def test_cli_with_base_ref_selects_from_the_git_diff(repo, monkeypatch, capsys):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=repo, check=True)
    subprocess.run(["git", "switch", "-qc", "pr"], cwd=repo, check=True)
    (repo / "tests/test_plan.py").write_text("from dataforge.deploy.plan import load  # x\n", encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "pr"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert affected.main(["--base-ref", "main"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [PLAN]
    assert f"{APPLY}: skipped" in captured.err


def test_cli_bad_base_ref_is_a_tool_error(repo, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert affected.main(["--base-ref", "nope"]) == 2
    assert "git diff against nope failed" in capsys.readouterr().err
