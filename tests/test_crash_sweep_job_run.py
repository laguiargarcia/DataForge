"""Crash sweep of the job-run state (df-ahua.5): `dataforge run` is killed at every database
commit, then the daemon starts once (its lifespan is where df-bw7 puts the recovery) and the run
and task rows are checked. The process that owned them is gone, so no row may stay in progress."""
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from dataforge.compat import db_url, global_db_url
from dataforge.db import ExecucaoORM, JobRunORM, init_db
from tests.crash_sweep import Step, WritePoints, crash_sweep

_JOB = "name: j\ntasks:\n  - {name: t, script: t.py}\n"
_COMMIT = "sqlalchemy.orm.Session.commit"   # every run and task row change is one commit


def _make_home(root: Path, name: str) -> Path:
    """A DATAFORGE_HOME with workspace w and job j: one python task that prints and ends."""
    ws = root / name / "workspaces" / "w"
    (ws / "jobs" / "j").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "workspace.yaml").write_text("name: w\n", encoding="utf-8")
    (ws / "jobs" / "j" / "job.yaml").write_text(_JOB, encoding="utf-8")
    (ws / "scripts" / "t.py").write_text("print('ok')\n", encoding="utf-8")
    return root / name


def _statuses(path: Path, orm) -> list[str]:
    with Session(init_db(db_url(path))) as session:
        return list(session.scalars(select(orm.status)))


def _restart_daemon(home: Path) -> None:
    """Start and stop the API app once with <home> as DATAFORGE_HOME (runs its lifespan)."""
    from dataforge.api import app
    saved = os.environ.get("DATAFORGE_HOME")
    os.environ["DATAFORGE_HOME"] = str(home)
    try:
        assert global_db_url() == db_url(home / "dataforge.db")   # the db the run wrote
        with TestClient(app):
            pass
    finally:
        if saved is None:
            del os.environ["DATAFORGE_HOME"]
        else:
            os.environ["DATAFORGE_HOME"] = saved


class _Left:
    """Which rows each crash left in progress after the restart: {"run running": [steps]}."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.steps: list[Step] = []
        self.found: dict[str, list[str]] = {}

    def record(self, home: Path, step: Step) -> None:
        _restart_daemon(home)
        rows = {"run": _statuses(home / "dataforge.db", JobRunORM),
                "task": _statuses(home / "workspaces/w/dataforge.db", ExecucaoORM)}
        for what, statuses in rows.items():
            for status in {"pending", "running"} & set(statuses):
                self.found.setdefault(f"{what} {status}", []).append(str(step))

    def assert_none(self, kind: str) -> None:
        assert self.found.get(kind, []) == [], f"{kind} after: {self.found[kind]}"


@pytest.fixture(scope="module")
def job_run_sweep(tmp_path_factory) -> _Left:
    left = _Left(tmp_path_factory.mktemp("job_run"))
    left.steps = crash_sweep("tests.job_run_crash_scenarios:run_job",
                             lambda name: _make_home(left.root, name), WritePoints([_COMMIT]),
                             left.record)
    return left


def test_job_run_sweep_reaches_every_commit_of_a_run(job_run_sweep):
    # run row inserted, set running, task row inserted, task row finished, run row finished
    assert len(job_run_sweep.steps) == 5
    assert _statuses(job_run_sweep.root / "dry/dataforge.db", JobRunORM) == ["success"]


@pytest.mark.xfail(strict=True, reason="df-bw7: a run killed after run_job set it 'running' stays "
                   "'running' forever; the daemon's startup re-publishes it instead of failing it")
def test_restart_after_a_killed_run_leaves_no_running_run(job_run_sweep):
    # df-bw7: the owning process is gone, so the row is provably orphaned (fix: lifespan).
    job_run_sweep.assert_none("run running")


@pytest.mark.xfail(strict=True, reason="df-ahua.5.6: a run killed before run_job starts stays "
                   "'pending' forever")
def test_restart_after_a_killed_run_leaves_no_pending_run(job_run_sweep):
    job_run_sweep.assert_none("run pending")


@pytest.mark.xfail(strict=True, reason="df-ahua.5.6: a task killed while its script runs stays "
                   "'running' forever in the workspace's execucoes table")
def test_restart_after_a_killed_run_leaves_no_running_task(job_run_sweep):
    job_run_sweep.assert_none("task running")
