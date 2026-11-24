"""Asking for a run without waiting for it, and reading a run's state by id (df-8x0m.23.13.1).

What a product calls through the CLI (`dataforge run --detach`, `dataforge run-state`): the request
records the run and hands it to a detached process, so the run goes on when the caller exits; the
state lives in the run history (`job_runs`), so it survives a restart of whoever asked.
"""
import time
import uuid
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import instance
from .compat import data_dir, db_url, global_db_url, logs_dir, resolve_workspace
from .db import ExecucaoORM, JobRunORM, ScheduleORM, init_db, utcnow
from .engine import run_job
from .orphans import reconcile_or_log
from .liveness import current_owner_text
from .parser import load_job

DB_FILE = "dataforge.db"

# Exit codes of the commands a program calls. 1 stays "the job ran and failed" (`dataforge run`).
EXIT_INVALID_REQUEST = 2
EXIT_STILL_RUNNING = 4  # `run-state --wait --timeout`: the time ran out before the run ended
EXIT_BUSY = 3  # reserved: today every request waits in the workspace queue (df-8x0m.23.15)

# How long the request waits for the detached process to take the run over (a Windows interpreter
# can take seconds to start).
START_SECONDS = 60.0
_POLL_SECONDS = 0.05

# job_runs.reason while a requested run waits for another job of its workspace to end. Only on a
# `pending` row: it is cleared when the run starts.
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


def _log_path() -> Path:
    return logs_dir() / "run-requests.log"


def request_run(workspace: str, job: str, dataforge_command: list[str], start_seconds: float | None = None) -> dict:
    """Record a run of `job`, start it in a detached process and answer its state.

    `dataforge_command` is the token list that starts this installation's CLI. Raises
    InvalidRequest, before recording anything, when the workspace or the job cannot be loaded.

    The row is born owned by this process, which stays until the detached one has taken it over:
    a sweep of orphaned runs (dataforge.orphans) in between sees a live owner. If the detached
    process never takes over, the run is ended as failed here, with the reason.
    """
    _load(workspace, job)
    run_id = str(uuid.uuid4())
    mine = current_owner_text()
    with Session(init_db(global_db_url())) as session:
        session.add(JobRunORM(
            id=run_id, workspace=workspace, job=job, status="pending", started_at=utcnow(),
            owner_id="system", trigger="manual", owner_process=mine,
        ))
        schedule = session.get(ScheduleORM, f"{workspace}:{job}")
        if schedule:
            schedule.last_run_at = utcnow()
        session.commit()
    instance.spawn_detached([*dataforge_command, "run-requested", workspace, job, run_id], _log_path())
    deadline = time.monotonic() + (START_SECONDS if start_seconds is None else start_seconds)
    while not _taken_over(run_id, mine):
        if time.monotonic() >= deadline:
            _never_started(run_id, mine)
            break
        time.sleep(_POLL_SECONDS)
    return run_state(run_id)


def _taken_over(run_id: str, requester: str) -> bool:
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        return run is None or run.owner_process != requester


def _never_started(run_id: str, requester: str) -> None:
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        if run and run.owner_process == requester and run.status == "pending":
            run.status, run.finished_at = "failed", utcnow()
            run.reason = f"The run process did not start (see {_log_path()})."
            session.commit()


def _take_over(run_id: str) -> bool:
    """Make this process the owner of a requested run; False when there is nothing left to run."""
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        if run is None or run.status != "pending":
            return False
        run.owner_process = current_owner_text()
        session.commit()
        return True


def _set_reason(run_id: str, reason: str | None) -> None:
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        if run:
            run.reason = reason
            session.commit()


def execute_requested(workspace: str, job: str, run_id: str) -> None:
    """Run what request_run recorded. Called in the detached process."""
    if not _take_over(run_id):
        return
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
    running, success, failed. A run whose process died is ended as failed, with the reason.
    """
    if not (data_dir() / DB_FILE).is_file():
        return None
    with Session(init_db(global_db_url())) as session:
        run = session.get(JobRunORM, run_id)
        if run is None:
            return None
        if run.finished_at is None:
            # A run whose process died says pending or running forever: end it before answering.
            reconcile_or_log(run.workspace)
            session.refresh(run)
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


def wait_for_end(run_id: str, timeout: float | None = None, poll_seconds: float = 0.5) -> dict | None:
    """run_state(), answered only when the run has ended, or when `timeout` seconds have passed
    (then the answer is the state in progress). One process follows the whole run, instead of one
    per question: starting the CLI costs about a second on Windows (df-8x0m.23.17)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        state = run_state(run_id)
        if state is None or state["finished_at"] is not None:
            return state
        if deadline is not None and time.monotonic() >= deadline:
            return state
        time.sleep(poll_seconds)
