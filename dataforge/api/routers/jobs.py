import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from dataforge.api.broadcaster import get_broadcaster
from dataforge.db import JobRunORM, ScheduleORM, init_db
from dataforge.engine import run_job
from dataforge.models import Job
from dataforge.dag import topological_levels
from dataforge.parser import (
    load_job,
    load_jobs,
    load_triggers,
    save_job,
    delete_job,
    is_valid_job_name,
)
from dataforge.compat import db_url, resolve_workspace, global_db_url

logger = logging.getLogger(__name__)
router = APIRouter()


def _validate_job_name(name: str) -> None:
    """Raise HTTP 400 if name has path separators or unsafe chars (job name is a directory name).

    Wraps parser.is_valid_job_name (the single regex source of truth) at the router boundary.
    """
    if not is_valid_job_name(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid job name '{name}': only alphanumerics, hyphens, and underscores are allowed",
        )


def _require_workspace(ws: str) -> Path:
    """Resolve a workspace dir or raise 404. NEW helper (not a reuse).

    list_jobs/trigger_job inline the resolve_workspace try/except + workspace.yaml-exists idiom;
    this consolidates it for the CRUD endpoints only. CRITICAL: never surface str(exc) —
    compat.resolve_workspace's ValueError embeds an ABSOLUTE filesystem path (compat.py), which
    would violate the no-path-leak convention (T-01.5.06-02). The fixed string below leaks nothing.
    """
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    if not (workspace_dir / "workspace.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    return workspace_dir


def _validate_job_dag(job: Job, workspace_dir: Path | None = None) -> list[str]:
    """Structural validation. BLOCKS (400) on duplicate task names / dangling dep / cycle.

    Returns a list of soft warnings (missing script files, missing type=job targets) for the
    caller to surface; an empty list when workspace_dir is None or nothing is amiss.
    """
    # Duplicate-task-name check MUST run BEFORE topological_levels: topological_levels builds
    # by_name = {p.name: p for p in pipelines}, which silently collapses duplicate names
    # (last one wins), so a job with two tasks named 'x' would pass topo-sort undetected.
    names = [t.name for t in job.tasks]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate task name(s) in job '{job.name}': {', '.join(dupes)}",
        )
    try:
        topological_levels(job.tasks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    warnings: list[str] = []
    if workspace_dir is not None:
        for task in job.tasks:
            # task.script is RELATIVE TO scripts/ (bare); it resolves against workspace_dir/scripts,
            # matching tasks/python_handler.py `script_path = workspace_dir / "scripts" / task.script`.
            if task.script and not (workspace_dir / "scripts" / task.script).exists():
                warnings.append(
                    f"Task '{task.name}': script '{task.script}' does not exist yet"
                )
            # Symmetric soft check for type=job targets (warn, never block — may be created later).
            if task.job and not (workspace_dir / "jobs" / task.job / "job.yaml").exists():
                warnings.append(
                    f"Task '{task.name}': referenced job '{task.job}' does not exist yet"
                )
    return warnings


def _scan_job_references(workspace_dir: Path, ws: str, job: str) -> dict:
    """List things that reference this job (D2). No cascade — informational only.

    SINGLE-WORKSPACE scan only: it inspects only load_triggers/load_jobs of workspace `ws`.
    Cross-workspace trigger refs are knowingly UNDER-REPORTED — TriggerJobEntry.workspace is a
    free-form str, so a trigger living in a different workspace that points at (ws, job) is MISSED.
    The global ScheduleORM row (keyed {ws}:{job}) is the only cross-workspace signal we catch.
    Accepted v1 limitation: the scan is informational (no hard gate), so a missed ref at worst
    under-reports the confirm dialog.
    """
    referencing_triggers = []
    for trig in load_triggers(workspace_dir):
        if any(e.job == job and e.workspace == ws for e in trig.jobs):
            referencing_triggers.append(trig.name)

    referencing_tasks = []
    for other in load_jobs(workspace_dir):
        if other.name == job:
            continue
        for task in other.tasks:
            if task.type == "job" and task.job == job:
                referencing_tasks.append(f"{other.name}.{task.name}")

    global_engine = init_db(global_db_url())
    with Session(global_engine) as session:
        has_schedule = session.get(ScheduleORM, f"{ws}:{job}") is not None

    return {
        "triggers": referencing_triggers,
        "tasks": referencing_tasks,
        "schedule": has_schedule,
    }


async def _run_job_background(
    run_id: str,
    job_name: str,
    workspace_dir: Path,
    ws_db_url: str,
    global_db_url: str,
    ws: str,
) -> None:
    """Run run_job in a thread; always writes a terminal status to JobRunORM."""
    broadcaster = get_broadcaster(ws)
    await broadcaster.publish({"workspace": ws, "job": job_name, "run_id": run_id, "status": "running"})
    try:
        job = load_job(workspace_dir, job_name)
        await asyncio.to_thread(run_job, job, workspace_dir, ws_db_url, run_id)

        global_engine = init_db(global_db_url)
        with Session(global_engine) as session:
            run = session.get(JobRunORM, run_id)
            terminal_status = run.status if run else "failed"
        await broadcaster.publish({"workspace": ws, "job": job_name, "run_id": run_id, "status": terminal_status})
    except Exception as exc:
        logger.error("Job run %s failed with exception: %s", run_id, exc)
        global_engine = init_db(global_db_url)
        with Session(global_engine) as session:
            run = session.get(JobRunORM, run_id)
            if run and run.status not in ("success", "failed"):
                run.status = "failed"
                run.finished_at = datetime.utcnow()
                session.commit()
        await broadcaster.publish({"workspace": ws, "job": job_name, "run_id": run_id, "status": "failed"})


@router.get("/{ws}/jobs")
def list_jobs(ws: str):
    """Return metadata for all jobs in a workspace, sorted by name.

    Each item: {name: str, parameters: list[dict]}.
    Filesystem paths are never leaked in error responses (T-01.5.06-02).
    """
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")

    if not (workspace_dir / "workspace.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")

    jobs = load_jobs(workspace_dir)
    return sorted(
        [{"name": job.name, "parameters": [p.model_dump() for p in job.parameters]} for job in jobs],
        key=lambda j: j["name"],
    )


@router.get("/{ws}/scripts")
def list_scripts(ws: str):
    """Return the workspace's Python scripts as BARE POSIX paths (relative to scripts/), sorted.

    scripts/ is the enforced root for all workspace scripts, so paths are emitted relative to
    the scripts dir (NOT the workspace) — bare like cleansed2curated/fact_fatura_mes.py, matching
    the task.script convention. Recurses scripts/ (scripts are NESTED under layer folders). Skips
    any path with a __pycache__ segment. A missing scripts/ dir is NOT an error — returns
    {"scripts": []}. Reuses _require_workspace for the clean 404 (no path leak, T-01.5.06-02).
    """
    workspace_dir = _require_workspace(ws)
    scripts_dir = workspace_dir / "scripts"
    if not scripts_dir.is_dir():
        return {"scripts": []}
    out = [
        p.relative_to(scripts_dir).as_posix()
        for p in scripts_dir.rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    return {"scripts": sorted(out)}


@router.post("/{ws}/jobs/{job}/run", status_code=202)
async def trigger_job(ws: str, job: str, background_tasks: BackgroundTasks):
    """Trigger async job execution. Returns 202 with execution_id within 100ms."""
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")

    if not (workspace_dir / "workspace.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")

    job_yaml = workspace_dir / "jobs" / job / "job.yaml"
    if not job_yaml.exists():
        raise HTTPException(
            status_code=404, detail=f"Job '{job}' not found in workspace '{ws}'"
        )

    run_id = str(uuid.uuid4())
    _db_url = global_db_url()
    global_engine = init_db(_db_url)
    ws_db_url = db_url(workspace_dir / "dataforge.db")

    with Session(global_engine) as session:
        session.add(JobRunORM(
            id=run_id,
            workspace=ws,
            job=job,
            status="pending",
            started_at=datetime.utcnow(),
            owner_id="system",
            trigger="manual",
        ))
        session.commit()

    background_tasks.add_task(
        _run_job_background, run_id, job, workspace_dir, ws_db_url, _db_url, ws
    )

    return JSONResponse(status_code=202, content={"execution_id": run_id})


@router.get("/{ws}/jobs/{job}/schedule")
def get_schedule(ws: str, job: str):
    """Return schedule state: cron, enabled, last_run_at, next_run_at."""
    global_engine = init_db(global_db_url())
    with Session(global_engine) as session:
        row = session.get(ScheduleORM, f"{ws}:{job}")
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No schedule configured for job '{job}' in workspace '{ws}'",
            )
        return {
            "cron": row.cron,
            "enabled": row.enabled,
            "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
            "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        }


@router.post("/{ws}/jobs", status_code=201)
def create_job(ws: str, job: Job):
    """Create a job: write jobs/<name>/job.yaml after name + DAG validation.

    409 if a job dir with that name already exists (net-new; no upsert). The existence check
    runs BEFORE save_job, which would otherwise clobber the existing job.
    """
    _validate_job_name(job.name)
    workspace_dir = _require_workspace(ws)
    if (workspace_dir / "jobs" / job.name / "job.yaml").exists():
        raise HTTPException(
            status_code=409, detail=f"Job '{job.name}' already exists in workspace '{ws}'"
        )
    warnings = _validate_job_dag(job, workspace_dir)
    save_job(workspace_dir, job)
    result = job.model_dump()
    if warnings:
        result["warnings"] = warnings
    return result


@router.get("/{ws}/jobs/{job}/references")
def get_job_references(ws: str, job: str):
    """Report referencing triggers / type=job tasks / schedule row (D2 — pre-delete check)."""
    _validate_job_name(job)
    workspace_dir = _require_workspace(ws)
    if not (workspace_dir / "jobs" / job / "job.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Job '{job}' not found in workspace '{ws}'")
    return _scan_job_references(workspace_dir, ws, job)


@router.get("/{ws}/jobs/{job}")
def get_job(ws: str, job: str):
    """Return the full job definition (name, tasks, retry, env_file, schedule, parameters)."""
    _validate_job_name(job)
    workspace_dir = _require_workspace(ws)
    if not (workspace_dir / "jobs" / job / "job.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Job '{job}' not found in workspace '{ws}'")
    return load_job(workspace_dir, job).model_dump()


@router.put("/{ws}/jobs/{job}")
def update_job(ws: str, job: str, body: Job):
    """Update an existing job. 404 if the job dir does not exist; 400 if body renames it (D1)."""
    _validate_job_name(job)
    _validate_job_name(body.name)
    workspace_dir = _require_workspace(ws)
    if not (workspace_dir / "jobs" / job / "job.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Job '{job}' not found in workspace '{ws}'")
    # D1: job name is immutable. Reject a rename rather than silently writing under the path
    # name (which would drop the body.name change and confuse the caller).
    if body.name != job:
        raise HTTPException(
            status_code=400,
            detail=(
                f"job name is immutable: body name '{body.name}' does not match path '{job}' "
                "(rename = delete + recreate)"
            ),
        )
    warnings = _validate_job_dag(body, workspace_dir)
    save_job(workspace_dir, body)  # body.name == job, enforced above (D1)
    result = body.model_dump()
    if warnings:
        result["warnings"] = warnings
    return result


@router.delete("/{ws}/jobs/{job}", status_code=204)
def remove_job(ws: str, job: str):
    """Delete a job directory. No cascade (D2): references are the GUI's concern to confirm."""
    _validate_job_name(job)
    workspace_dir = _require_workspace(ws)
    if not (workspace_dir / "jobs" / job / "job.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Job '{job}' not found in workspace '{ws}'")
    delete_job(workspace_dir, job)
