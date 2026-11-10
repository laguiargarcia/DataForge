"""Tests for scripts/quality/gate.py: the ratchet arithmetic, the project rules and update/check.

The tools themselves (ruff, vulture, import-linter) are replaced by fixed findings, so these tests
run without them and judge only the gate's own logic.
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
