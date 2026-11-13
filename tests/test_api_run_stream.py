"""GET /api/executions/{run_id}/stream on the path every GUI run detail takes (df-ahua.16.3):
start a run through the API, follow its SSE stream on a real socket to the end, check each
snapshot and that the stream closes. The snapshot's shape is a contract with the run detail panel
(df-ahua.16.1: the panel showed no log because the stream never sent `output`)."""
import json
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from dataforge.api.routers.executions import _task_snapshot
from dataforge.compat import db_url, global_db_url
from dataforge.db import ExecucaoORM, JobRunORM, init_db, utcnow
from tests.live_api import (GATED_JOB, GATED_OUTPUT, GATED_TASK, add_gated_job, release_gated_job,
                            serve_api)
from tests.process_owners import dead_owner_text

_FOLLOW_TIMEOUT = 30.0


def _follow(base: str, run_id: str, on_snapshot=lambda s: None,
            timeout: float = _FOLLOW_TIMEOUT) -> tuple[list[dict], bool]:
    """Read the run's stream until it closes or `timeout` passes: (snapshots, closed)."""
    snapshots = []
    deadline = time.monotonic() + timeout
    with (httpx.Client(timeout=10) as client,
          client.stream("GET", f"{base}/api/executions/{run_id}/stream") as response):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                snapshots.append(json.loads(line[len("data: "):]))
                on_snapshot(snapshots[-1])
            if time.monotonic() > deadline:
                return snapshots, False
    return snapshots, True


def _start(base: str, job: str = GATED_JOB) -> str:
    response = httpx.post(f"{base}/api/workspaces/test_ws/jobs/{job}/run", timeout=10)
    assert response.status_code == 202
    return response.json()["execution_id"]


def _release_once_running(workspace_dir: Path, exit_code: int = 0):
    """An on_snapshot that releases the task the first time a snapshot shows it running."""
    def on_snapshot(snapshot: dict) -> None:
        if any(t["status"] == "running" for t in snapshot["tasks"]):
            release_gated_job(workspace_dir, exit_code)
    return on_snapshot


def _outputs(snapshots: list[dict]) -> list[str]:
    return [t["output"] for s in snapshots for t in s["tasks"] if "output" in t]


def test_a_run_started_through_the_api_streams_to_success_and_closes(workspace_dir):
    add_gated_job(workspace_dir)
    with serve_api() as base:
        run_id = _start(base)
        snapshots, closed = _follow(base, run_id, _release_once_running(workspace_dir))

    assert closed
    assert all(s["run_id"] == run_id for s in snapshots)
    statuses = [s["status"] for s in snapshots]
    assert statuses[-1] == "success"
    assert all(status in ("pending", "running") for status in statuses[:-1])
    assert "running" in statuses   # the stream showed the run in progress, then its end
    # The task as the run detail sees it: one row, in progress first, then its end.
    task_states = [t["status"] for s in snapshots for t in s["tasks"]]
    assert task_states[0] == "running"
    assert task_states[-1] == "success"
    last = snapshots[-1]["tasks"]
    assert [t["name"] for t in last] == ["extract"]
    assert last[0]["started_at"]
    assert last[0]["finished_at"]
    # df-ahua.16.1: the task's output reaches the stream, once, not again on every tick.
    assert _outputs(snapshots) == [GATED_OUTPUT]


def test_a_failed_run_streams_to_failed_with_the_task_output_and_closes(workspace_dir):
    add_gated_job(workspace_dir)
    with serve_api() as base:
        run_id = _start(base)
        snapshots, closed = _follow(base, run_id, _release_once_running(workspace_dir, 1))

    assert closed
    assert snapshots[-1]["status"] == "failed"
    assert snapshots[-1]["tasks"][0]["status"] == "failed"
    assert _outputs(snapshots) == [GATED_OUTPUT]


def test_a_finished_run_opened_later_gets_one_snapshot_with_the_output_and_closes(workspace_dir):
    """What the run detail opened from History receives: the run is over, so one snapshot."""
    add_gated_job(workspace_dir)
    release_gated_job(workspace_dir)
    with serve_api() as base:
        run_id = _start(base)
        _follow(base, run_id)
        snapshots, closed = _follow(base, run_id)

    assert closed
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "success"
    assert snapshots[0]["tasks"][0]["output"] == GATED_OUTPUT


def _add_run(run_id: str, owner: str | None = None) -> None:
    with Session(init_db(global_db_url())) as session:
        session.add(JobRunORM(id=run_id, workspace="test_ws", job=GATED_JOB, status="running",
                              started_at=utcnow(), **({"owner_process": owner} if owner else {})))
        session.commit()


def test_deleting_the_run_mid_stream_closes_the_stream(workspace_dir):
    with serve_api() as base:
        _add_run("r-deleted")   # owned by this live process: never reconciled

        def delete_it(_snapshot):
            with Session(init_db(global_db_url())) as session:
                run = session.get(JobRunORM, "r-deleted")
                if run is not None:
                    session.delete(run)
                    session.commit()

        snapshots, closed = _follow(base, "r-deleted", delete_it)

    assert closed
    assert [s["status"] for s in snapshots] == ["running"]


@pytest.mark.xfail(strict=True, reason=(
    "df-ahua.16.11: the run stream never reconciles, so a run whose process died while the "
    "server runs stays 'running' in it and the stream never closes"))
def test_the_stream_of_a_run_whose_process_died_ends_failed_and_closes(workspace_dir):
    """The run and its task are written after the server started (so its startup reconciliation
    cannot fix them) by a process that has ended: df-bw7 says such a run is failed."""
    with serve_api() as base:
        dead = dead_owner_text()
        _add_run("r-dead", owner=dead)
        with Session(init_db(db_url(workspace_dir / "dataforge.db"))) as session:
            session.add(ExecucaoORM(job=GATED_JOB, task=GATED_TASK, status="running",
                                    inicio=utcnow(), run_id="r-dead", owner_process=dead))
            session.commit()
        snapshots, closed = _follow(base, "r-dead", timeout=4.0)

    assert closed, f"still open after 4 s; statuses {[s['status'] for s in snapshots]}"
    assert snapshots[-1]["status"] == "failed"


def test_a_task_output_is_sent_once_and_again_only_when_it_changes():
    """An orphan's output gets the reason appended (df-bw7): that change reaches the stream."""
    sent: dict[int, str] = {}
    row = ExecucaoORM(id=7, task="t", status="running", inicio=utcnow(), output=None)
    assert "output" not in _task_snapshot(row, sent)
    row.output = ""
    assert "output" not in _task_snapshot(row, sent)
    row.output, row.status = "done\n", "success"
    assert _task_snapshot(row, sent)["output"] == "done\n"
    assert "output" not in _task_snapshot(row, sent)   # unchanged: not resent on the next tick
    row.output = "done\ninterrupted"
    assert _task_snapshot(row, sent)["output"] == "done\ninterrupted"
    other = ExecucaoORM(id=8, task="t", status="success", inicio=utcnow(), output="done\n")
    assert _task_snapshot(other, sent)["output"] == "done\n"   # a retry's row is its own
