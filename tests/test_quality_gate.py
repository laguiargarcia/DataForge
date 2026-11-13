"""Tests for scripts/quality/gate.py: the ratchet arithmetic, the project rules and update/check.

The tools themselves (ruff, vulture, import-linter, deptry) are replaced by fixed findings, so these tests
run without them and judge only the gate's own logic. One deptry test also runs the real tool where installed.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "scripts" / "quality" / "gate.py"
_spec = importlib.util.spec_from_file_location("quality_gate", GATE)
gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gate  # dataclasses look the module up while building Finding
_spec.loader.exec_module(gate)


def finding(file, code, line=1):
    return gate.Finding(file, code, line, "msg")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fake repo: baselines under tmp_path, tools replaced by a settable list of findings."""
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(gate, "LOCAL_BASELINE", tmp_path / "baseline.local.json")
    monkeypatch.setattr(gate, "_check_tool_versions", lambda: None)
    state = {"findings": [], "public": ["pub.py", "other.py"], "private": ["workspaces/Priv/x.py"]}
    monkeypatch.setattr(gate, "public_files", lambda: state["public"])
    monkeypatch.setattr(gate, "private_files", lambda: state["private"])
    monkeypatch.setattr(gate, "collect_full", lambda public, private: state["findings"])
    monkeypatch.setattr(gate, "ruff", lambda files: [f for f in state["findings"] if f.file in files and f.code[0] != "v"])
    monkeypatch.setattr(gate, "project_rules", lambda files: [])
    return state


def write_baseline(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------- arithmetic


def test_increases_reports_new_keys_and_higher_counts_only():
    old = {"a.py": {"F401": 2, "B904": 1}}
    new = {"a.py": {"F401": 3, "B904": 1}, "b.py": {"E9": 1}}
    assert gate.increases(old, new) == [("a.py", "F401", 2, 3), ("b.py", "E9", 0, 1)]


def test_decreases_reports_lower_and_vanished_counts():
    old = {"a.py": {"F401": 2}, "gone.py": {"B904": 1}}
    new = {"a.py": {"F401": 1}}
    assert gate.decreases(old, new) == [("a.py", "F401", 2, 1), ("gone.py", "B904", 1, 0)]


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("unused function 'f' (60% confidence)", "vulture:unused-function"),
        ("unused variable 'x' (100% confidence)", "vulture:unused-variable"),
        ("unreachable code after 'return' (100% confidence, 2 lines)", "vulture:unreachable-code"),
        ('invalid syntax at "# type: omit"', "vulture:parse-error"),
    ],
)
def test_vulture_codes(message, code):
    assert gate._vulture_code(message) == code


# ---------------------------------------------------------------------------- project rules


def rules_for(tmp_path, monkeypatch, rel, source):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(source, encoding="utf-8")
    return [(f.code, f.line) for f in gate.project_rules([rel])]


def test_suppression_needs_a_reason(tmp_path, monkeypatch):
    source = (
        "import os  # noqa: F401\n"
        "import sys  # noqa: F401 -- re-exported for callers\n"
        "x = 1  # type: ignore\n"
        "y = 2  # pragma: no cover -- unreachable on Linux\n"
        's = "# noqa: not a comment"\n'
    )
    assert rules_for(tmp_path, monkeypatch, "pkg/m.py", source) == [("DF001", 1), ("DF001", 3)]


def test_sqlite_url_only_in_compat_and_tests(tmp_path, monkeypatch):
    source = 'a = "sqlite:///x.db"\nb = f"sqlite:///{a}"\n# sqlite:/// in a comment is fine\n'
    assert rules_for(tmp_path, monkeypatch, "pkg/m.py", source) == [("DF002", 1), ("DF002", 2)]
    assert rules_for(tmp_path, monkeypatch, "dataforge/compat.py", source) == []
    assert rules_for(tmp_path, monkeypatch, "tests/test_x.py", source) == []


def test_a_file_that_does_not_tokenize_is_left_to_ruff(tmp_path, monkeypatch):
    """An unterminated string or bytes that are not UTF-8 stop tokenizing: no project finding
    for that file, and the files after it are still judged."""
    (tmp_path / "bad.py").write_bytes(b"x = 1  # noqa: F401\ns = '''never closed\n")
    (tmp_path / "latin.py").write_bytes(b"x = 'caf\xe9'  # noqa: F401\n")
    assert rules_for(tmp_path, monkeypatch, "ok.py", "y = 2  # noqa: E501\n") == [("DF001", 1)]
    found = gate.project_rules(["bad.py", "latin.py", "ok.py"])
    assert [(f.file, f.code) for f in found] == [("ok.py", "DF001")]


# ---------------------------------------------------------------------------- update


def test_update_lowers_fixed_counts_and_keeps_new_ones_out(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 2, "B904": 1}})
    repo["findings"] = [finding("pub.py", "F401"), finding("pub.py", "C901"), finding("other.py", "E9")]
    assert gate.update(allow_increase=False) == 0
    assert gate.load(gate.BASELINE) == {"pub.py": {"F401": 1}}


def test_update_allow_increase_accepts_and_splits_private_paths(repo):
    repo["findings"] = [finding("pub.py", "F401"), finding("workspaces/Priv/x.py", "B904")]
    assert gate.update(allow_increase=True) == 0
    assert gate.load(gate.BASELINE) == {"pub.py": {"F401": 1}}
    assert gate.load(gate.LOCAL_BASELINE) == {"workspaces/Priv/x.py": {"B904": 1}}


def test_update_refuses_a_broken_import_contract(repo):
    repo["findings"] = [finding("pyproject.toml", "import-contract", 0)]
    assert gate.update(allow_increase=True) == 1
    assert not gate.BASELINE.exists()


# ---------------------------------------------------------------------------- check


def test_check_passes_at_the_baseline(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 1}})
    repo["findings"] = [finding("pub.py", "F401")]
    assert gate.check([], None, full=False) == 0


def test_check_fails_on_a_new_finding(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 1}})
    repo["findings"] = [finding("pub.py", "F401"), finding("pub.py", "F401", 9)]
    assert gate.check([], None, full=False) == 1


def test_check_lowers_the_baseline_by_itself(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 2, "B904": 1}, "gone.py": {"E9": 1}})
    write_baseline(gate.LOCAL_BASELINE, {"workspaces/Priv/x.py": {"B904": 3}})
    repo["findings"] = [finding("pub.py", "F401"), finding("pub.py", "B904"), finding("workspaces/Priv/x.py", "B904")]
    assert gate.check([], None, full=False) == 0
    assert gate.load(gate.BASELINE) == {"pub.py": {"F401": 1, "B904": 1}}
    assert gate.load(gate.LOCAL_BASELINE) == {"workspaces/Priv/x.py": {"B904": 1}}


def test_lowering_never_raises_and_new_findings_still_fail(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 2}})
    repo["findings"] = [finding("pub.py", "B904")]
    assert gate.check([], None, full=False) == 1
    assert gate.load(gate.BASELINE) == {}


def test_fast_check_lowers_only_its_own_files(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 2, "vulture:unused-function": 1}, "other.py": {"F401": 5}})
    repo["findings"] = [finding("pub.py", "F401")]
    assert gate.check([str(gate.ROOT / "pub.py")], None, full=False) == 0
    assert gate.load(gate.BASELINE) == {
        "other.py": {"F401": 5}, "pub.py": {"F401": 1, "vulture:unused-function": 1}
    }


def test_full_check_of_files_ignores_other_files(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 1}})
    repo["findings"] = [finding("pub.py", "F401"), finding("other.py", "B904")]
    assert gate.check([str(gate.ROOT / "pub.py")], None, full=True) == 0
    assert gate.check([str(gate.ROOT / "other.py")], None, full=True) == 1


def test_full_check_of_files_still_reports_a_broken_contract(repo):
    repo["findings"] = [finding("pyproject.toml", "import-contract", 0)]
    assert gate.check([str(gate.ROOT / "pub.py")], None, full=True) == 1


def test_fast_check_leaves_full_only_counts_alone(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 1, "vulture:unused-function": 3}})
    repo["findings"] = [finding("pub.py", "F401")]
    assert gate.check([str(gate.ROOT / "pub.py")], None, full=False) == 0


def test_base_ref_fails_when_the_committed_baseline_rose(repo, monkeypatch):
    write_baseline(gate.BASELINE, {"pub.py": {"F401": 2}})
    repo["findings"] = [finding("pub.py", "F401"), finding("pub.py", "F401", 5)]
    monkeypatch.setattr(gate, "_git_show", lambda ref, rel: json.dumps({"pub.py": {"F401": 1}}))
    assert gate.check([], "origin/main", full=False) == 1
    monkeypatch.setattr(gate, "_git_show", lambda ref, rel: json.dumps({"pub.py": {"F401": 2}}))
    assert gate.check([], "origin/main", full=False) == 0


# ---------------------------------------------------------------- arguments never read as options


def test_ruff_and_vulture_get_the_files_after_double_dash(monkeypatch):
    """A file whose name starts with '-' (e.g. '--fix.py') must reach the tool as a path."""
    import subprocess
    calls = []
    monkeypatch.setattr(gate, "_run", lambda cmd: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "[]", ""))
    files = ["--fix.py", "pub.py"]
    gate.ruff(files)
    gate.vulture(files)
    assert [cmd[cmd.index("--") + 1:] for cmd in calls] == [files, files]


def test_git_show_reads_a_ref_never_an_option(tmp_path, monkeypatch):
    """A ref like '--output=<file>' would make git show write that file."""
    import subprocess
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "baseline.json").write_text("{}\n", encoding="utf-8")
    subprocess.run([*git, "add", "baseline.json"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate._git_show("HEAD", "baseline.json") == "{}\n"
    with pytest.raises(ValueError, match="not a git ref name"):
        gate._git_show(f"--output={tmp_path / 'leaked'}", "baseline.json")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("leaked")]


@pytest.mark.parametrize("ref", ["origin/main", "main", "v1.2", "feature/df-1.2_x"])
def test_base_ref_accepts_ref_names(ref):
    assert gate._ref_name(ref) == ref


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-p", "", "a b", "main;rm"])
def test_base_ref_rejects_what_is_not_a_ref_name(ref, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gate.py", "check", f"--base-ref={ref}"])
    with pytest.raises(SystemExit) as exc:
        gate.main()
    assert exc.value.code == 2
    assert "not a git ref name" in capsys.readouterr().err


# ---------------------------------------------------------------- private files (df-grvi)


def _git_repo(path):
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return lambda *args: subprocess.run(["git", *args], cwd=path, check=True)


def _write(path, text="x = 1\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_private_files_lists_a_plain_ignored_workspace(tmp_path, monkeypatch):
    _git_repo(tmp_path)
    _write(tmp_path / ".gitignore", "workspaces/Priv/\n")
    _write(tmp_path / "workspaces" / "Priv" / "scripts" / "a.py")
    _write(tmp_path / "workspaces" / "Priv" / "notes.txt")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.private_files() == ["workspaces/Priv/scripts/a.py"]


def test_private_files_looks_inside_a_workspace_that_is_its_own_repo(tmp_path, monkeypatch):
    """git lists a nested repository as one 'dir/' entry, so its files used to vanish from the gate."""
    _git_repo(tmp_path)
    _write(tmp_path / ".gitignore", "workspaces/Nested/\n")
    nested = tmp_path / "workspaces" / "Nested"
    git = _git_repo(nested)
    _write(nested / ".gitignore", "data/\n")
    _write(nested / "scripts" / "tracked.py")
    git("add", "scripts/tracked.py")
    _write(nested / "scripts" / "new.py")            # untracked, not ignored: work in progress
    _write(nested / "data" / "generated.py")         # ignored by the nested repo: not code to gate
    _write(nested / "scripts" / "__pycache__" / "c.py")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.private_files() == ["workspaces/Nested/scripts/new.py", "workspaces/Nested/scripts/tracked.py"]


# ---------------------------------------------------------------------------- deptry


def _fake_deptry(monkeypatch, issues, returncode=1):
    """Replace the deptry run: write `issues` where --json-output points, return `returncode`."""
    import subprocess
    calls = []

    def run(cmd):
        calls.append(cmd)
        Path(cmd[cmd.index("--json-output") + 1]).write_text(json.dumps(issues), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, "", "deptry broke")

    monkeypatch.setattr(gate, "_run", run)
    return calls


def test_deptry_issues_become_findings(monkeypatch):
    calls = _fake_deptry(monkeypatch, [
        {"error": {"code": "DEP001", "message": "'nope' imported but missing from the dependency definitions"},
         "module": "nope", "location": {"file": "scripts/x.py", "line": 3, "column": 8}},
        {"error": {"code": "DEP002", "message": "'unused' defined as a dependency but not used in the codebase"},
         "module": "unused", "location": {"file": "pyproject.toml", "line": None, "column": None}},
    ])
    found = gate.dependencies(["dataforge/a.py", "scripts/x.py"])
    assert [(f.file, f.code, f.line) for f in found] == [
        ("scripts/x.py", "deptry:DEP001", 3),
        ("pyproject.toml", "deptry:DEP002", 0),
    ]
    assert gate.count(found) == {"scripts/x.py": {"deptry:DEP001": 1}, "pyproject.toml": {"deptry:DEP002": 1}}
    assert calls[0][calls[0].index("--known-first-party") + 1] == "x"


def test_deptry_clean_tree_gives_no_findings(monkeypatch):
    _fake_deptry(monkeypatch, [], returncode=0)
    assert gate.dependencies(["dataforge/a.py"]) == []


@pytest.mark.parametrize("returncode", [2, 101])
def test_deptry_crash_stops_the_gate(monkeypatch, returncode):
    _fake_deptry(monkeypatch, [], returncode=returncode)
    with pytest.raises(SystemExit, match="deptry failed"):
        gate.dependencies([])


def test_sibling_modules_are_script_stems_outside_the_package_and_tests():
    files = ["dataforge/engine.py", "tests/conftest.py", "scripts/testloop/common.py",
             "workspaces/S/scripts/report_lib/__init__.py", "setup.py"]
    assert gate._sibling_modules(files) == ["common", "report_lib", "setup"]


def test_a_new_dependency_finding_fails_the_full_check(repo):
    repo["findings"] = [finding("pyproject.toml", "deptry:DEP002", 0)]
    assert gate.check([], None, full=True) == 1


def test_fast_check_leaves_deptry_counts_alone(repo):
    write_baseline(gate.BASELINE, {"pub.py": {"deptry:DEP001": 1}})
    repo["findings"] = []
    assert gate.check([str(gate.ROOT / "pub.py")], None, full=False) == 0
    assert gate.load(gate.BASELINE) == {"pub.py": {"deptry:DEP001": 1}}


def test_real_deptry_reports_an_undeclared_import_and_nothing_on_a_clean_tree(tmp_path, monkeypatch):
    """End to end with the pinned deptry: the gate installs it (scripts/quality/requirements.txt);
    the test jobs do not, so there this one test is skipped and the mocked ones above stand."""
    pytest.importorskip("deptry")
    _write(tmp_path / "pyproject.toml", '[project]\nname = "t"\nversion = "0"\ndependencies = []\n')
    _write(tmp_path / "scripts" / "a.py", "import json\nimport b\n")   # stdlib and a sibling: fine
    _write(tmp_path / "scripts" / "b.py")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    clean = ["scripts/a.py", "scripts/b.py"]
    assert gate.dependencies(clean) == []

    _write(tmp_path / "scripts" / "c.py", "import undeclared_pkg_df\n")
    found = gate.dependencies([*clean, "scripts/c.py"])
    assert gate.count(found) == {"scripts/c.py": {"deptry:DEP001": 1}}
