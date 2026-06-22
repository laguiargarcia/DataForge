import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .compat import global_db_url
from .conditions import should_run
from .dag import topological_levels
from .db import ExecucaoORM, JobRunORM, init_db
from .models import Job, Task
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


def run_job(
    job: Job,
    workspace_dir: Path,
    db_url: str,
    run_id: str | None = None,
) -> dict[str, TaskResult]:
    db_engine = init_db(db_url)
    global_engine = init_db(global_db_url())

    # Synthesize run_id when caller didn't provide one (B-F1). Allows handlers
    # to filter ExecucaoORM rows by run_id even on local CLI runs. JobRunORM
    # creation remains caller's responsibility — the finalize block silently
    # no-ops via `if run:` when no global row exists (R2-F6).
    if run_id is None:
        run_id = f"local-{uuid.uuid4().hex[:12]}"

    inactive = {t.name for t in job.tasks if not t.active}

    active_tasks = [
        t.model_copy(update={
            "depends_on": [e for e in t.depends_on if e.task not in inactive]
        })
        for t in job.tasks if t.active
    ]
    levels = topological_levels(active_tasks)

    has_child: set[str] = set()
    for t in active_tasks:
        for e in t.depends_on:
            has_child.add(e.task)
    leaves = {t.name for t in active_tasks if t.name not in has_child}

    parent_status: dict[str, str] = {}
    results: dict[str, TaskResult] = {name: None for name in inactive}
    error_task_ran_nonzero = False

    with Session(global_engine) as session:
        run = session.get(JobRunORM, run_id)
        if run:
            run.status = "running"
            session.commit()

    for level in levels:
        runnable: list[Task] = []
        skipped_in_level: list[Task] = []
        for t in level:
            if should_run(t, parent_status):
                runnable.append(t)
            else:
                skipped_in_level.append(t)

        if skipped_in_level:
            now = datetime.utcnow()
            with Session(db_engine) as s:
                for t in skipped_in_level:
                    s.add(ExecucaoORM(
                        job=job.name, task=t.name,
                        status="skipped",
                        inicio=now, fim=now,
                        duracao_segundos=0.0,
                        run_id=run_id,
                    ))
                    results[t.name] = "skipped"
                    parent_status[t.name] = "skipped"
                s.commit()

        if runnable:
            with ThreadPoolExecutor() as executor:
                futures = {
                    executor.submit(run_task_with_retry, t, job, workspace_dir, db_url, run_id): t
                    for t in runnable
                }
                for future in as_completed(futures):
                    t = futures[future]
                    rc = future.result()
                    results[t.name] = rc
                    parent_status[t.name] = "success" if rc == 0 else "failed"
                    if rc != 0 and get_handler(t.type).forces_run_failure:
                        error_task_ran_nonzero = True

    any_leaf_failed = any(
        results.get(name) not in (0, "skipped", None) for name in leaves
    )
    final_status = "failed" if (any_leaf_failed or error_task_ran_nonzero) else "success"
    with Session(global_engine) as session:
        run = session.get(JobRunORM, run_id)
        if run:
            run.status = final_status
            run.finished_at = datetime.utcnow()
            session.commit()

    return results
