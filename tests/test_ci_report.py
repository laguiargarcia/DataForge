"""scripts/ci_report.py: which report files belong to which CI job (df-ahua.24)."""
import importlib.util
from pathlib import Path

import pytest

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


VALIDATE_OUTPUT = {
    "✓ Valid (3 files)": (
        "02:15:09.648 INF likec4·lang workspace: found 3 source files\n"
        "02:15:09.891 INF likec4·c4:validate ✓ Valid (3 files)\n"
        "02:15:09.892 INF likec4·c4:validate validate 460ms\n"),
    "✗ Invalid (3 files, 1 errors)": (
        "02:15:19.033 ERR likec4 Invalid /w/arquitetura/spec.c4\n"
        "    Line 38: Could not resolve reference to ElementKind named 'bogus'.\n"
        "02:15:19.265 ERR likec4·c4:validate ✗ Invalid (3 files, 1 errors)\n"
        "02:15:19.266 INF likec4·c4:validate validate 435ms\n"),
    "✗ Invalid (3 files, 0 errors, 1 warnings (max 0))": (
        "02:16:02.371 ERR likec4·c4:validate ✗ Invalid (3 files, 0 errors, 1 warnings (max 0))\n"
        "02:16:02.372 INF likec4·c4:validate validate 431ms\n"),
}


@pytest.mark.parametrize(("expected", "output"), VALIDATE_OUTPUT.items(), ids=["valid", "error", "warning"])
def test_the_likec4_validate_outcome_is_the_summary_not_its_timing_line(expected, output):
    """`likec4 validate` (job arquitetura, df-il2f) ends on a timing line after its outcome; the row
    starts at the outcome's mark, without the log's timestamp."""
    assert ci_report.summary(output) == expected


def test_a_line_without_the_validate_outcome_is_summarized_whole():
    assert ci_report.summary("== 1 failed, 2 passed in 3.1s ==\n") == "1 failed, 2 passed in 3.1s"
