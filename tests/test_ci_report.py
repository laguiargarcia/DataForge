"""scripts/ci_report.py: which report files belong to which CI job (df-ahua.24)."""
import importlib.util
import json
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


# The drawing comment (df-8x0m.23.38): its own marker, created, edited, deleted; a fork only gets the summary.
REPO, PR = "dono/repo", "7"


def _comment(id_, body, kind="Bot"):
    return {"id": id_, "body": body, "user": {"type": kind}}


def _main(tmp_path, monkeypatch, comments, drawing="", **env_over):
    """Run main with report/desenho.md holding `drawing` and the PR holding `comments`; the gh calls
    it made, as (method, path, body), the listing of the comments as ("LIST", path, None). `env_over`
    replaces any variable of the environment."""
    report = tmp_path / "report"
    report.mkdir()
    (report / ci_report.DRAWING_FILE).write_text(drawing, encoding="utf-8")
    monkeypatch.setattr(ci_report, "REPORT_DIR", report)
    env = {"GITHUB_REPOSITORY": REPO, "PR": PR, "RUN_URL": "u", "SHA": "abcdef0123",
           "RESULTS": json.dumps({"arquitetura": {"result": "success"}}), "DESENHO": "success",
           "HEAD_REPO": REPO, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")} | env_over
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []

    def gh(*args, payload=None):
        if args[:3] == ("api", "--paginate", "--slurp"):
            calls.append(("LIST", args[3], None))
            return json.dumps([comments])
        method, path = args[2], args[3]
        calls.append((method, path, json.loads(payload)["body"] if payload else None))
        return ""

    monkeypatch.setattr(ci_report, "gh", gh)
    ci_report.main()
    return calls


def _writes(calls):
    assert calls[0] == ("LIST", f"repos/{REPO}/issues/{PR}/comments", None)
    return calls[1:]


DRAWN = "### Drawing changes\n\n**Arrows**: 1 new\n- ➕ `a -> b`\n"


def test_a_pr_that_changes_the_drawing_gets_its_own_comment(tmp_path, monkeypatch):
    calls = _writes(_main(tmp_path, monkeypatch, [_comment(1, ci_report.MARKER)], drawing=DRAWN))
    assert [c[:2] for c in calls] == [("PATCH", f"repos/{REPO}/issues/comments/1"), ("POST", f"repos/{REPO}/issues/{PR}/comments")]
    assert calls[1][2] == f"{ci_report.DRAWING_MARKER}\n{DRAWN}"
    assert ci_report.MARKER not in calls[1][2]


def test_the_drawing_comment_is_edited_and_a_persons_comment_left_alone(tmp_path, monkeypatch):
    comments = [_comment(1, ci_report.MARKER), _comment(2, ci_report.DRAWING_MARKER, "User"), _comment(3, ci_report.DRAWING_MARKER)]
    calls = _writes(_main(tmp_path, monkeypatch, comments, drawing=DRAWN))
    assert [c[:2] for c in calls] == [("PATCH", f"repos/{REPO}/issues/comments/1"), ("PATCH", f"repos/{REPO}/issues/comments/3")]


def test_a_pr_that_stops_changing_the_drawing_loses_the_comment(tmp_path, monkeypatch):
    comments = [_comment(1, ci_report.MARKER), _comment(3, ci_report.DRAWING_MARKER), _comment(4, ci_report.DRAWING_MARKER)]
    calls = _writes(_main(tmp_path, monkeypatch, comments, drawing=""))
    assert calls[1:] == [("DELETE", f"repos/{REPO}/issues/comments/3", None), ("DELETE", f"repos/{REPO}/issues/comments/4", None)]


def test_a_pr_that_never_changed_the_drawing_gets_no_drawing_comment(tmp_path, monkeypatch):
    calls = _writes(_main(tmp_path, monkeypatch, [], drawing="\n"))
    assert [c[:2] for c in calls] == [("POST", f"repos/{REPO}/issues/{PR}/comments")]
    assert calls[0][2].startswith(ci_report.MARKER)


@pytest.mark.parametrize("desenho", ["failure", "skipped", ""])
def test_a_failed_drawing_diff_leaves_the_drawing_comment_as_it_was(tmp_path, monkeypatch, desenho):
    comments = [_comment(1, ci_report.MARKER), _comment(3, ci_report.DRAWING_MARKER)]
    calls = _writes(_main(tmp_path, monkeypatch, comments, drawing="", DESENHO=desenho))
    assert [c[:2] for c in calls] == [("PATCH", f"repos/{REPO}/issues/comments/1")]


def test_a_stale_model_is_said_on_the_drawing_comment(tmp_path, monkeypatch):
    calls = _writes(_main(tmp_path, monkeypatch, [], drawing=DRAWN, RESULTS=json.dumps({"arquitetura": {"result": "failure"}})))
    assert "Job `arquitetura` did not pass" in calls[1][2]


def test_a_long_drawing_comment_is_cut_under_githubs_limit(tmp_path, monkeypatch):
    calls = _writes(_main(tmp_path, monkeypatch, [], drawing=DRAWN + "- x\n" * 20000))
    assert len(calls[1][2]) < 65536
    assert calls[1][2].endswith("(cut: the job summary has it whole)\n")
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert summary.endswith("- x\n" * 20000)
    assert ci_report.MARKER not in summary


# The CI report quotes job output: a failing test of this file shows the drawing marker in a details block.
QUOTING = (f"{ci_report.MARKER}\n### ❌\n<details><summary>out</summary>\n\n```text\n"
           f"assert '{ci_report.DRAWING_MARKER}'\n```\n</details>")


def test_a_ci_report_quoting_the_drawing_marker_is_not_deleted_when_the_drawing_is_unchanged(tmp_path, monkeypatch):
    calls = _writes(_main(tmp_path, monkeypatch, [_comment(1, QUOTING)], drawing=""))
    assert [c[:2] for c in calls] == [("PATCH", f"repos/{REPO}/issues/comments/1")]


def test_a_ci_report_quoting_the_drawing_marker_is_not_overwritten_by_the_drawing(tmp_path, monkeypatch):
    calls = _writes(_main(tmp_path, monkeypatch, [_comment(1, QUOTING)], drawing=DRAWN))
    assert [c[:2] for c in calls] == [("PATCH", f"repos/{REPO}/issues/comments/1"), ("POST", f"repos/{REPO}/issues/{PR}/comments")]
    assert calls[0][2].startswith(ci_report.MARKER)


def test_a_forks_pr_writes_both_texts_to_the_job_summary_and_calls_no_api(tmp_path, monkeypatch):
    calls = _main(tmp_path, monkeypatch, [], drawing=DRAWN, HEAD_REPO="outro/repo")
    assert calls == []
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert ci_report.MARKER in summary
    assert "- ➕ `a -> b`" in summary


@pytest.mark.parametrize(("name", "value"), [("PR", "7; rm -rf /"), ("PR", "../1"), ("GITHUB_REPOSITORY", "a/b/../c d")])
def test_a_pr_number_or_repo_out_of_pattern_stops_before_any_call(tmp_path, monkeypatch, name, value):
    with pytest.raises(SystemExit, match=name):
        _main(tmp_path, monkeypatch, [], drawing=DRAWN, **{name: value})
