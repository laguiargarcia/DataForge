"""scripts/ci_report.py: which report files belong to which CI job (df-ahua.24)."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ci_report", Path(__file__).resolve().parent.parent / "scripts" / "ci_report.py")
ci_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci_report)


def test_a_job_does_not_take_the_files_of_a_job_named_after_it(tmp_path, monkeypatch):
    for name in ["tests-engine", "tests-gui", "tests-windows", "tests-windows-extra"]:
        (tmp_path / f"{name}.txt").write_text("1 passed\n", encoding="utf-8")
    monkeypatch.setattr(ci_report, "REPORT_DIR", tmp_path)
    jobs = ("tests", "tests-windows")
    assert [p.stem for p in ci_report.outputs("tests", jobs)] == ["tests-engine", "tests-gui"]
    assert [p.stem for p in ci_report.outputs("tests-windows", jobs)] == ["tests-windows", "tests-windows-extra"]


def test_the_report_row_of_tests_lists_only_its_own_parts(tmp_path, monkeypatch):
    (tmp_path / "tests-engine.txt").write_text("== 3 passed ==\n", encoding="utf-8")
    (tmp_path / "tests-windows.txt").write_text("== 1 failed, 2 passed ==\n", encoding="utf-8")
    monkeypatch.setattr(ci_report, "REPORT_DIR", tmp_path)
    body = ci_report.build({"tests": {"result": "success"}, "tests-windows": {"result": "failure"}}, "u", "abc")
    assert "| `tests` | ✅ success | 3 passed |" in body
    assert "| `tests-windows` | ❌ failure | 1 failed, 2 passed |" in body
