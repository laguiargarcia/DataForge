import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import asc
from sqlalchemy.orm import Session

from dataforge.db import ExecucaoORM, JobRunORM, init_db
from dataforge.compat import data_dir, db_url, global_db_url

logger = logging.getLogger(__name__)
router = APIRouter()


def _workspace_db_url(workspace_name: str) -> str:
    return db_url(data_dir() / "workspaces" / workspace_name / "dataforge.db")


def _serialize_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _task_snapshot(row: ExecucaoORM, sent_output: dict[int, str]) -> dict:
    """One task of a run snapshot. `output` is there only when this stream has not sent that
    row's current output yet: the run detail shows it as the task's log (df-ahua.16.1). A task
    stores its output once, when it ends (and an orphan's gets the reason appended), and the
    column has no size limit, so repeating it every tick would resend the whole log each second;
    an absent `output` means "unchanged", and the panel keeps the log it has."""
    task = {
        "name": row.task,
        "status": row.status,
        "started_at": _serialize_dt(row.inicio),
        "finished_at": _serialize_dt(row.fim),
    }
    if row.output and sent_output.get(row.id) != row.output:
        task["output"] = sent_output[row.id] = row.output
    return task


async def _sse_event_generator(run_id: str) -> AsyncGenerator[str, None]:
    """
    Poll DB every 1 second, emit full snapshot SSE events (each task's output only when it
    changed, see _task_snapshot). Closes when job reaches success or failed. New Session per tick.
    """
    _db_url = global_db_url()
    global_engine = init_db(_db_url)
    sent_output: dict[int, str] = {}

    while True:
        with Session(global_engine) as g_session:
            run = g_session.get(JobRunORM, run_id)
            if run is None:
                break
            run_status = run.status
            workspace_name = run.workspace

        ws_db_url = _workspace_db_url(workspace_name)
        ws_engine = init_db(ws_db_url)
        with Session(ws_engine) as ws_session:
            task_rows = (
                ws_session.query(ExecucaoORM)
                .filter(ExecucaoORM.run_id == run_id)
                .order_by(asc(ExecucaoORM.inicio))
                .all()
            )
            tasks_data = [_task_snapshot(row, sent_output) for row in task_rows]

        snapshot = {"run_id": run_id, "status": run_status, "tasks": tasks_data}
        yield f"data: {json.dumps(snapshot)}\n\n"

        if run_status in ("success", "failed"):
            break

        await asyncio.sleep(1)


@router.get("/{run_id}/stream")
async def stream_execution(run_id: str):
    """SSE stream of job run state. Full snapshot per tick (a task's output only when it changed).
    Closes on success/failed."""
    global_engine = init_db(global_db_url())

    with Session(global_engine) as session:
        run = session.get(JobRunORM, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Execution '{run_id}' not found")

    return StreamingResponse(
        _sse_event_generator(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/output")
async def get_execution_output(run_id: str):
    """Return per-task stdout/stderr for a given execution."""
    global_engine = init_db(global_db_url())

    with Session(global_engine) as session:
        run = session.get(JobRunORM, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Execution '{run_id}' not found")
        workspace_name = run.workspace

    ws_engine = init_db(_workspace_db_url(workspace_name))

    with Session(ws_engine) as ws_session:
        task_rows = (
            ws_session.query(ExecucaoORM)
            .filter(ExecucaoORM.run_id == run_id)
            .order_by(asc(ExecucaoORM.inicio))
            .all()
        )
        return [
            {"task": row.task, "status": row.status, "output": row.output}
            for row in task_rows
        ]
