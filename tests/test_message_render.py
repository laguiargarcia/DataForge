from datetime import datetime

import pytest

from dataforge.tasks.message.render import build_variables, render


def test_build_variables_always_populated():
    vars = build_variables(
        workspace="ws", job="j", trigger="manual",
        run_id="r1", status="failed",
        started_at=datetime(2026, 5, 28, 6, 0, 0),
        finished_at=datetime(2026, 5, 28, 6, 1, 30),
        task_results=[
            {"name": "a", "status": "success", "rc": 0, "output": "ok"},
            {"name": "b", "status": "failed", "rc": 1, "output": "boom\nstack\n"},
            {"name": "c", "status": "skipped", "rc": None, "output": None},
        ],
    )
    assert vars["workspace"] == "ws"
    assert vars["run_id"] == "r1"
    assert vars["status"] == "failed"
    assert vars["duration_seconds"] == 90.0
    assert vars["failed_tasks"] == "b"
    assert vars["succeeded_tasks"] == "a"
    assert vars["skipped_tasks"] == "c"
    assert "b" in vars["failed_tasks_summary"]
    assert "boom" in vars["failed_tasks_summary"]


def test_empty_lists_render_empty_string():
    vars = build_variables(
        workspace="ws", job="j", trigger="cron",
        run_id="r1", status="success",
        started_at=datetime(2026, 5, 28, 6, 0, 0),
        finished_at=datetime(2026, 5, 28, 6, 0, 0),
        task_results=[{"name": "a", "status": "success", "rc": 0, "output": "ok"}],
    )
    assert vars["failed_tasks"] == ""
    assert vars["failed_tasks_summary"] == ""


def test_render_substitutes():
    out = render("Run {run_id} {status}", {"run_id": "r1", "status": "failed"})
    assert out == "Run r1 failed"


def test_render_missing_variable_raises_useful_error():
    with pytest.raises(KeyError):
        render("{never_defined}", {"run_id": "r1"})


def test_timestamps_local_with_offset():
    vars = build_variables(
        workspace="ws", job="j", trigger="manual",
        run_id="r1", status="success",
        started_at=datetime(2026, 5, 28, 6, 0, 0),
        finished_at=datetime(2026, 5, 28, 6, 0, 1),
        task_results=[],
    )
    assert "T" in vars["started_at"]
    assert vars["started_at"][-3] == ":" or vars["started_at"].endswith(("Z",))
