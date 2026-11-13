import time
from collections.abc import Callable
import uuid
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy.orm import Session

from .compat import global_db_url
from .conditions import should_run
from .dag import topological_levels
from .db import ExecucaoORM, JobRunORM, init_db, utcnow
from .models import Job, Task
from .runlock import workspace_run_lock
from .tasks import get_handler


TaskResult = int | str | None


def run_task(
    task: Task,
    job: Job,
    workspace_dir: Path,
    db_url: str,
    run_id: str | None = None,
) -> int:
    handler = get_handler(task.type)
    return handler.run(task, job, workspace_dir, db_url, run_id)


def run_task_with_retry(
    task: Task,
    job: Job,
    workspace_dir: Path,
    db_url: str,
    run_id: str | None = None,
) -> int:
    rc = 1
    for attempt in range(task.retry.attempts):
        rc = run_task(task, job, workspace_dir, db_url, run_id=run_id)
        if rc == 0:
            return 0
        if attempt < task.retry.attempts - 1:
            time.sleep(task.retry.delay_seconds)
    return rc


def _prune_inactive(job: Job) -> tuple[set[str], list[Task]]:
    """The inactive task names, and the active tasks with their edges to inactive ones dropped."""
    inactive = {t.name for t in job.tasks if not t.active}
    active_tasks = [
        t.model_copy(update={
            "depends_on": [e for e in t.depends_on if e.task not in inactive]
        })
        for t in job.tasks if t.active
    ]
    return inactive, active_tasks


def _failed(result: TaskResult) -> bool:
    return isinstance(result, int) and result != 0


def run_status(job: Job, results: dict[str, TaskResult]) -> str:
    """The run's outcome from its tasks' results: "failed" or "success".

    A failed task fails the run unless a dependent whose edge to it accepts `failed` (`when:
    failed` or `completed`) ran: that dependent took the failure over, as an alert handler does.
    A failed leaf has no dependent, so it always fails the run. A failed `error` task fails it
    regardless. Skipped tasks are neutral, so a failure that only skipped its chain is not
    hidden behind skipped leaves (df-h864).
    """
    _, active_tasks = _prune_inactive(job)
    taken_over = {
        e.task for t in active_tasks if isinstance(results.get(t.name), int)
        for e in t.depends_on if "failed" in e.when
    }
    for t in active_tasks:
        if _failed(results.get(t.name)) and (
                t.name not in taken_over or get_handler(t.type).forces_run_failure):
            return "failed"
    return "success"


def _set_run_status(global_engine, run_id: str, status: str, finished: bool = False) -> None:
    """Update the caller's JobRunORM row; a no-op when no global row exists (R2-F6)."""
    with Session(global_engine) as session:
        run = session.get(JobRunORM, run_id)
        if run:
            run.status = status
            if finished:
                run.finished_at = utcnow()
            session.commit()


@dataclass
class _JobRun:
    """One run of a job: what its tasks need to execute, and what they have produced so far."""
    job: Job
    workspace_dir: Path
    db_url: str
    run_id: str
    results: dict[str, TaskResult]
    parent_status: dict[str, str] = field(default_factory=dict)


def _split_level(level: list[Task], parent_status: dict[str, str]) -> tuple[list[Task], list[Task]]:
    """The level's tasks whose dependency conditions hold, and the ones to skip."""
    runnable: list[Task] = []
    skipped: list[Task] = []
    for t in level:
        if should_run(t, parent_status):
            runnable.append(t)
        else:
            skipped.append(t)
    return runnable, skipped


def _record_skipped(db_engine, run: _JobRun, skipped: list[Task]) -> None:
    now = utcnow()
    with Session(db_engine) as s:
        for t in skipped:
            s.add(ExecucaoORM(
                job=run.job.name, task=t.name,
                status="skipped",
                inicio=now, fim=now,
                duracao_segundos=0.0,
                run_id=run.run_id,
            ))
            run.results[t.name] = "skipped"
            run.parent_status[t.name] = "skipped"
        s.commit()


def _run_parallel(run: _JobRun, runnable: list[Task]) -> None:
    """Run one level's tasks in parallel, recording each one's result."""
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(run_task_with_retry, t, run.job, run.workspace_dir, run.db_url,
                            run.run_id): t
            for t in runnable
        }
        for future in as_completed(futures):
            t = futures[future]
            rc = future.result()
            run.results[t.name] = rc
            run.parent_status[t.name] = "success" if rc == 0 else "failed"


def run_job(  # noqa: PLR0913 -- on_running and on_wait are the two optional caller hooks
    job: Job,
    workspace_dir: Path,
    db_url: str,
    run_id: str | None = None,
    on_running: Callable[[], None] | None = None,
    on_wait: Callable[[], None] | None = None,
) -> dict[str, TaskResult]:
    # Synthesize run_id when caller didn't provide one (B-F1). Allows handlers
    # to filter ExecucaoORM rows by run_id even on local CLI runs. JobRunORM
    # creation remains caller's responsibility — the finalize block silently
    # no-ops via `if run:` when no global row exists (R2-F6).
    if run_id is None:
        run_id = f"local-{uuid.uuid4().hex[:12]}"

    # on_wait: called once if another job of this workspace holds the run lock (the CLI says so).
    with workspace_run_lock(workspace_dir, run_id, on_wait=on_wait):
        return _run_job_locked(job, workspace_dir, db_url, run_id, on_running)


def _run_job_locked(
    job: Job,
    workspace_dir: Path,
    db_url: str,
    run_id: str,
    on_running: Callable[[], None] | None = None,
) -> dict[str, TaskResult]:
    db_engine = init_db(db_url)
    global_engine = init_db(global_db_url())
    inactive, active_tasks = _prune_inactive(job)
    levels = topological_levels(active_tasks)

    run = _JobRun(job, workspace_dir, db_url, run_id, results=dict.fromkeys(inactive))

    _set_run_status(global_engine, run_id, "running")
    if on_running is not None:
        on_running()

    for level in levels:
        runnable, skipped_in_level = _split_level(level, run.parent_status)
        if skipped_in_level:
            _record_skipped(db_engine, run, skipped_in_level)
        if runnable:
            _run_parallel(run, runnable)

    _set_run_status(global_engine, run_id, run_status(job, run.results), finished=True)
    return run.results
