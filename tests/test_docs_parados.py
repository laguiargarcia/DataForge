"""Tests for scripts/quality/docs_parados.py: the gate warns, never fails, on a doc whose code changed."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "scripts" / "quality" / "docs_parados.py"
_spec = importlib.util.spec_from_file_location("docs_parados", SRC)
dp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dp
_spec.loader.exec_module(dp)


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def doc(root, name, code, status="pronto"):
    f = root / "docs" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"---\ntopic: T\nread_when: W\ncode: [{', '.join(code)}]\nstatus: {status}\n---\n\n# T\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    """A repo whose main has docs and code; HEAD is a branch, `main` stands in for origin/main."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "pkg" / "motor").mkdir(parents=True)
    (tmp_path / "pkg" / "motor" / "run.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "other.py").write_text("y = 1\n", encoding="utf-8")
    doc(tmp_path, "architecture.md", ["pkg"])
    doc(tmp_path, "caixas/motor.md", ["pkg/motor"], status="esboço")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "feat")
    return tmp_path


def test_covered_file_changed_doc_not_warns_naming_both(repo):
    (repo / "pkg" / "motor" / "run.py").write_text("x = 2\n", encoding="utf-8")
    lines = dp.relatorio(repo, "main")
    assert "warning: docs/caixas/motor.md was not changed, but the code it covers was: pkg/motor/run.py" in lines


def test_doc_changed_too_no_warning(repo):
    (repo / "pkg" / "motor" / "run.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "docs" / "caixas" / "motor.md").write_text("---\ncode: [pkg/motor]\nstatus: pronto\n---\n", encoding="utf-8")
    git(repo, "commit", "-qam", "both")
    assert not [line for line in dp.relatorio(repo, "main") if line.startswith("warning:")]


def test_only_most_specific_doc_warns(repo):
    (repo / "pkg" / "motor" / "run.py").write_text("x = 2\n", encoding="utf-8")
    warnings = [line for line in dp.relatorio(repo, "main") if line.startswith("warning:")]
    assert len(warnings) == 1
    assert "docs/caixas/motor.md" in warnings[0]


def test_broader_doc_warns_for_file_only_it_covers(repo):
    (repo / "pkg" / "new.py").write_text("z = 1\n", encoding="utf-8")  # untracked counts
    warnings = [line for line in dp.relatorio(repo, "main") if line.startswith("warning:")]
    assert warnings == ["warning: docs/architecture.md was not changed, but the code it covers was: pkg/new.py"]


def test_sketch_count_shown(repo):
    assert dp.relatorio(repo, "main")[0] == "docs: 1 of 2 in sketch (status esboço)"


def test_no_merge_base_skips(repo):
    assert "stale-doc check skipped" in dp.relatorio(repo, "nope")[-1]


def load_gate(repo, monkeypatch):
    gate_spec = importlib.util.spec_from_file_location("quality_gate_dp", SRC.parent / "gate.py")
    gate = importlib.util.module_from_spec(gate_spec)
    sys.modules[gate_spec.name] = gate
    gate_spec.loader.exec_module(gate)
    monkeypatch.setattr(gate, "ROOT", repo)
    monkeypatch.setattr(gate, "_check_tool_versions", lambda: None)
    monkeypatch.setattr(gate, "_judge", lambda paths, full: ([], {}, [], []))
    monkeypatch.setattr(dp, "BASE", "main")
    monkeypatch.setitem(sys.modules, "docs_parados", dp)
    return gate


def test_gate_check_exits_zero_with_warning(repo, monkeypatch, capsys):
    gate = load_gate(repo, monkeypatch)
    (repo / "pkg" / "motor" / "run.py").write_text("x = 2\n", encoding="utf-8")
    assert gate.check([], None, full=False) == 0
    out = capsys.readouterr().out
    assert "warning: docs/caixas/motor.md" in out
    assert "none new; 1 stale docs)" in out


def test_gate_check_survives_a_failing_report(repo, monkeypatch, capsys):
    gate = load_gate(repo, monkeypatch)

    def boom(root, base=None):
        raise RuntimeError("kaput")

    monkeypatch.setattr(dp, "relatorio", boom)
    assert gate.check([], None, full=False) == 0
    out = capsys.readouterr().out
    assert "docs: stale-doc check failed: kaput" in out
    assert "quality gate: ok" in out


def test_tie_warns_every_doc_of_the_longest_path(repo):
    doc(repo, "caixas/motor2.md", ["pkg/motor"])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "second doc")
    git(repo, "checkout", "-q", "-B", "main")
    git(repo, "checkout", "-qb", "feat2")
    (repo / "pkg" / "motor" / "run.py").write_text("x = 2\n", encoding="utf-8")
    warned = sorted(line.split()[1] for line in dp.relatorio(repo, "main") if line.startswith("warning:"))
    assert warned == ["docs/caixas/motor.md", "docs/caixas/motor2.md"]


def test_file_entry_in_code_is_more_specific_than_its_folder(repo):
    doc(repo, "run.md", ["pkg/motor/run.py"])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "file doc")
    git(repo, "checkout", "-q", "-B", "main")
    git(repo, "checkout", "-qb", "feat2")
    (repo / "pkg" / "motor" / "run.py").write_text("x = 2\n", encoding="utf-8")
    warned = [line.split()[1] for line in dp.relatorio(repo, "main") if line.startswith("warning:")]
    assert warned == ["docs/run.md"]


def test_file_moved_out_of_a_covered_folder_warns_its_doc(repo):
    git(repo, "mv", "pkg/motor/run.py", "pkg/run.py")
    git(repo, "commit", "-qm", "move")
    warned = {line.split()[1] for line in dp.relatorio(repo, "main") if line.startswith("warning:")}
    assert "docs/caixas/motor.md" in warned
