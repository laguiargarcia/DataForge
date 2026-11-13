"""Correct the record of runs whose process died (df-bw7, df-ahua.5.6).

A run or task row still `pending`/`running` whose owner process has provably ended
(`dataforge.liveness.owner_is_gone`) can never finish: nothing will write its outcome. Such a row
becomes `failed` with REASON. That is the whole correction: nothing is re-run (df-413), and a row
whose owner may be alive is never touched, so the run's own final update never races with this.

What an orphan becomes:
- run row (`job_runs`, global database): `failed`, `reason` = REASON, `finished_at` = the last moment
  the run was seen alive: its start, or the latest start/end of its task rows when later.
- task row (`execucoes`, both workspace stores: `<ws>/dataforge.db` written by `run`,
  `scheduled-run` and the API, and `<ws>/data/dataforge.db` written by `jobs run`): `failed`,
  `fim` = its `inicio` (nothing later is known), REASON appended to `output`, `duracao_segundos`
  left as it was (unknown).

Each row is judged by its own owner, so a task row is corrected even when its run has no run row
(`jobs run`). Running this twice, or from two processes at once, gives the same result.
"""
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .compat import data_dir, db_url, list_workspaces, resolve_workspace
from .db import ExecucaoORM, JobRunORM, init_db
from .liveness import Owner, current_owner, owner_is_gone, parse_owner

REASON = ("interrupted: the process running it ended before it finished "
          "(killed, closed or powered off); nothing was re-run")
IN_PROGRESS = ("pending", "running")
_DB_FILE = "dataforge.db"

logger = logging.getLogger(__name__)


def _task_stores(workspace_dir: Path) -> list[Path]:
    """The workspace's run-history databases that exist (never creates one)."""
    paths = [workspace_dir / _DB_FILE, workspace_dir / "data" / _DB_FILE]
    return [p for p in paths if p.is_file()]


def _orphans(rows: list, me: Owner) -> list:
    return [r for r in rows if owner_is_gone(parse_owner(r.owner_process), me)]


def reconcile_task_rows(url: str, me: Owner | None = None) -> int:
    """Fail the orphaned in-progress task rows of one database; return how many."""
    me = me or current_owner()
    engine = init_db(url)
    try:
        with Session(engine) as session:
            dead = _orphans(session.query(ExecucaoORM)
                            .filter(ExecucaoORM.status.in_(IN_PROGRESS)).all(), me)
            for row in dead:
                row.status = "failed"
                row.fim = row.inicio
                row.output = f"{row.output}\n{REASON}" if row.output else REASON
            if dead:
                session.commit()
            return len(dead)
    finally:
        engine.dispose()


def _last_seen(run: JobRunORM) -> datetime:
    """The latest moment the run's rows show it alive: its start or a task's start/end."""
    try:
        workspace_dir = resolve_workspace(run.workspace)
    except ValueError:   # the workspace is gone or its name is invalid
        return run.started_at
    path = workspace_dir / _DB_FILE
    if not path.is_file():
        return run.started_at
    engine = init_db(db_url(path))
    try:
        with Session(engine) as session:
            tasks = session.query(ExecucaoORM).filter(ExecucaoORM.run_id == run.id).all()
            moments = [t for row in tasks for t in (row.inicio, row.fim) if t is not None]
    finally:
        engine.dispose()
    return max([run.started_at, *moments])


def reconcile(workspace: str | None = None) -> list[str]:
    """Fail every orphaned in-progress run and task row, of one workspace or of all; return the
    ids of the runs it failed. Task rows go first, so a run ends at its tasks' last moment."""
    me = current_owner()
    if workspace is None:
        dirs = list_workspaces()
    else:
        try:
            dirs = [resolve_workspace(workspace)]
        except ValueError:
            dirs = []
    for workspace_dir in dirs:
        for path in _task_stores(workspace_dir):
            reconcile_task_rows(db_url(path), me)

    engine = init_db(db_url(data_dir() / _DB_FILE))
    try:
        with Session(engine) as session:
            query = session.query(JobRunORM).filter(JobRunORM.status.in_(IN_PROGRESS))
            if workspace is not None:
                query = query.filter(JobRunORM.workspace == workspace)
            dead = _orphans(query.all(), me)
            for run in dead:
                run.status = "failed"
                run.reason = REASON
                run.finished_at = _last_seen(run)
            if dead:
                session.commit()
            return [run.id for run in dead]
    finally:
        engine.dispose()


def reconcile_or_log(workspace: str | None = None) -> list[str]:
    """reconcile(), for callers that must go on when it fails (daemon start, history reads): a
    broken database is logged and the rows stay as they were."""
    try:
        return reconcile(workspace)
    except Exception:
        logger.exception("Reconciling orphaned runs failed; their rows stay as they were")
        return []
