"""Asking for a run without waiting for it, and reading a run's state by id (df-8x0m.23.13.1).

What a product calls through the CLI (`dataforge run --detach`, `dataforge run-state`): the request
records the run and hands it to a detached process, so the run goes on when the caller exits; the
state lives in the run history (`job_runs`), so it survives a restart of whoever asked.
"""
import subprocess
import sys
import uuid
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .compat import data_dir, db_url, global_db_url, resolve_workspace
from .db import ExecucaoORM, JobRunORM, init_db, utcnow
from .engine import run_job
from .liveness import current_owner_text
from .parser import load_job

DB_FILE = "dataforge.db"

# Exit codes of the commands a program calls. 1 stays "the job ran and failed" (`dataforge run`).
EXIT_INVALID_REQUEST = 2
EXIT_BUSY = 3  # reserved: today every request waits in the workspace queue (df-8x0m.23.15)

# job_runs.reason while a requested run waits for another job of its workspace to end.
WAITING = "waiting_for_workspace"
_REASON_CHARS = 500


class InvalidRequest(Exception):
    """The request names a workspace or a job that cannot run; nothing was recorded."""


def _load(workspace: str, job: str):
    try:
        workspace_path = resolve_workspace(workspace)
        return workspace_path, load_job(workspace_path, job)
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc
    except FileNotFoundError as exc:
        raise InvalidRequest(f"Job '{job}' not found in workspace '{workspace}'.") from exc
    except (ValidationError, yaml.YAMLError, TypeError) as exc:
        raise InvalidRequest(f"Job '{job}' of workspace '{workspace}' is not valid: {exc}") from exc


def _spawn_detached(command: list[str]) -> None:
    """Start `command` so that it outlives this process and its console."""
    options: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, close_fds=True, **options)


def request_run(workspace: str, job: str, dataforge_command: list[str]) -> dict:
    """Record a run of `job` and start it in a detached process; answer its state at once.

    `dataforge_command` is the token list that starts this installation's CLI. Raises
    InvalidRequest, before recording anything, when the workspace or the job cannot be loaded.
    """
    _load(workspace, job)
    run_id = str(uuid.uuid4())
    with Session(init_db(global_db_url())) as session:
        # No owner yet: the detached process takes the row over when it starts. With this
        # process as owner, a sweep after it exits would read the run as orphaned.
        session.add(JobRunORM(
            id=run_id, workspace=workspace, job=job, status="pending", started_at=utcnow(),
            owner_id="system", trigger="manual", owner_process=None,
        ))
        session.commit()
    _spawn_detached([*dataforge_command, "run-requested", workspace, job, run_id])
    return run_state(run_id)


def _set_reason(run_id: str, reason: str | None, take_over: bool = False) -> None:
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        if run:
            run.reason = reason
            if take_over:
                run.owner_process = current_owner_text()
            session.commit()


def execute_requested(workspace: str, job: str, run_id: str) -> None:
    """Run what request_run recorded. Called in the detached process."""
    _set_reason(run_id, None, take_over=True)
    try:
        workspace_path, loaded = _load(workspace, job)
        run_job(
            loaded, workspace_path, db_url(workspace_path / DB_FILE), run_id,
            on_running=lambda: _set_reason(run_id, None),
            on_wait=lambda: _set_reason(run_id, WAITING),
        )
    except Exception as exc:  # nobody watches this process: the history must say why it died
        with Session(init_db(global_db_url())) as session:
            run = session.get(JobRunORM, run_id)
            if run and run.finished_at is None:
                run.status, run.finished_at, run.reason = "failed", utcnow(), f"{type(exc).__name__}: {exc}"[:_REASON_CHARS]
                session.commit()
        raise


def _failure(workspace: str, run_id: str) -> tuple[str | None, str | None]:
    """The first failed task of the run and the end of what it printed."""
    try:
        ws_db = resolve_workspace(workspace) / DB_FILE
    except ValueError:
        return None, None
    if not Path(ws_db).is_file():
        return None, None
    with Session(init_db(db_url(ws_db))) as session:
        row = session.scalars(
            select(ExecucaoORM).where(ExecucaoORM.run_id == run_id, ExecucaoORM.status == "failed")
            .order_by(ExecucaoORM.id)
        ).first()
        if row is None:
            return None, None
        return row.task, (row.output or "").strip()[-_REASON_CHARS:] or None


def run_state(run_id: str) -> dict | None:
    """The run's state as the fixed-field object a program reads; None for an unknown id.

    `state` is one of: pending (accepted, not started), waiting (in the workspace queue),
    running, success, failed, or whatever other end status the history holds (an interrupted run).
    """
    if not (data_dir() / DB_FILE).is_file():
        return None
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        if run is None:
            return None
        state = "waiting" if run.status == "pending" and run.reason == WAITING else run.status
        failed_task, output = _failure(run.workspace, run_id) if run.status == "failed" else (None, None)
        reason = None if run.reason == WAITING else run.reason
        return {
            "run_id": run.id,
            "workspace": run.workspace,
            "job": run.job,
            "state": state,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "failed_task": failed_task,
            "reason": reason or output,
        }
