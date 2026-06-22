import logging
from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from dataforge.db import JobRunORM, init_db
from dataforge.parser import load_jobs
from dataforge.compat import global_db_url, list_workspaces

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _get_last_run_status(session: Session, workspace_name: str, job_name: str) -> str | None:
    run = (
        session.query(JobRunORM)
        .filter(JobRunORM.workspace == workspace_name, JobRunORM.job == job_name)
        .order_by(desc(JobRunORM.started_at))
        .first()
    )
    return run.status if run else None


@router.get("/")
async def list_workspaces_endpoint():
    """List all workspaces and their jobs with last-run status.

    The trigger detail page populates its workspace dropdown from this endpoint
    by plucking the ``name`` field from each item (Plan 01.5-06 Task 2, Option 1).
    No separate /names endpoint is added — the GUI ignores the embedded ``jobs``
    field and avoids API surface bloat.
    """
    workspace_paths = list_workspaces()
    global_engine = init_db(global_db_url())

    result = []
    with Session(global_engine) as session:
        for ws_path in workspace_paths:
            ws_name = ws_path.name
            jobs = []
            try:
                loaded_jobs = load_jobs(ws_path)
                for job in loaded_jobs:
                    last_status = _get_last_run_status(session, ws_name, job.name)
                    jobs.append({"name": job.name, "last_run_status": last_status})
            except Exception as exc:
                logger.warning("Error loading jobs for workspace %s: %s", ws_name, exc)

            result.append({"name": ws_name, "jobs": jobs})

    return result


@router.get("/{ws}/executions")
async def list_executions(
    ws: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return paginated execution history for a workspace, newest first."""
    global_engine = init_db(global_db_url())

    with Session(global_engine) as session:
        base_query = (
            session.query(JobRunORM)
            .filter(JobRunORM.workspace == ws)
            .order_by(desc(JobRunORM.started_at))
        )
        total = base_query.count()
        runs = base_query.offset(offset).limit(limit).all()

        items = []
        for run in runs:
            duration = None
            if run.finished_at and run.started_at:
                duration = (run.finished_at - run.started_at).total_seconds()
            items.append({
                "run_id": run.id,
                "job": run.job,
                "status": run.status,
                "started_at": _serialize_dt(run.started_at),
                "finished_at": _serialize_dt(run.finished_at),
                "duration_seconds": duration,
                "trigger": run.trigger,
            })

    return {"items": items, "total": total}
