"""run_job's bookkeeping: the global JobRunORM row (running, then the final status) and the
ExecucaoORM row a skipped task leaves. Tasks run through a fake runner unless the handler is
what matters, so each test states only the outcome it needs."""
import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from dataforge import engine
from dataforge.db import ExecucaoORM, JobRunORM, init_db
from dataforge.engine import run_job, run_task_with_retry
from dataforge.models import Job, RetryConfig, Task

NO_RETRY = RetryConfig(attempts=1, delay_seconds=0)


def task(name, depends_on=None, **kw):
    kw.setdefault("script", f"{name}.py")
    return Task(name=name, depends_on=depends_on or [], retry=NO_RETRY, **kw)


def job(*tasks):
    return Job(name="j", tasks=list(tasks), retry=NO_RETRY)


@pytest.fixture
def outcomes(monkeypatch):
    """Map task name -> return code for the fake runner; tasks not listed succeed."""
    codes: dict[str, int] = {}
    monkeypatch.setattr(engine, "run_task_with_retry",
                        lambda t, *_a, **_k: codes.get(t.name, 0) if t.type != "error"
                        else run_task_with_retry(t, *_a, **_k))
    return codes


@pytest.fixture
def run_row(global_engine):
    """Create the pending JobRunORM row a caller (API, scheduler) would, and read it back."""
    def make(run_id="run-1"):
        with Session(global_engine) as s:
            s.add(JobRunORM(id=run_id, workspace="ws", job="j", status="pending"))
            s.commit()
        return run_id

    def read(run_id="run-1"):
        with Session(global_engine) as s:
            return s.get(JobRunORM, run_id)

    make.read = read
    return make


def rows(db_url, name):
    with Session(init_db(db_url)) as s:
        return s.scalars(select(ExecucaoORM).where(ExecucaoORM.task == name)).all()


def test_a_failed_leaf_fails_the_run_and_stamps_its_end(tmp_path, db_url, run_row, outcomes):
    outcomes["t1"] = 1
    run_job(job(task("t1")), tmp_path, db_url, run_id=run_row())
    row = run_row.read()
    assert row.status == "failed"
    assert row.finished_at is not None


def test_an_error_task_fails_the_run_even_when_its_handler_task_succeeds(
        tmp_path, db_url, run_row, outcomes):
    """The error task is not a leaf here: a child handles its failure and succeeds. The run is
    still failed, because an error task that ran is a failure by definition."""
    t1 = task("t1", type="error", script=None, params={"message": "trip-wire"})
    t2 = task("t2", depends_on=[{"task": "t1", "when": "failed"}])
    results = run_job(job(t1, t2), tmp_path, db_url, run_id=run_row())
    assert results == {"t1": 1, "t2": 0}
    assert run_row.read().status == "failed"


def test_the_run_reads_running_and_unfinished_while_its_tasks_execute(
        tmp_path, db_url, run_row, monkeypatch):
    seen: list[tuple] = []

    def runner(*_a, **_k):
        row = run_row.read()
        seen.append((row.status, row.finished_at))
        return 0

    monkeypatch.setattr(engine, "run_task_with_retry", runner)
    run_job(job(task("t1")), tmp_path, db_url, run_id=run_row())
    assert seen == [("running", None)]
    assert run_row.read().status == "success"


def test_a_skipped_leaf_does_not_fail_the_run(tmp_path, db_url, run_row, outcomes):
    t2 = task("t2", depends_on=[{"task": "t1", "when": "failed"}])
    results = run_job(job(task("t1"), t2), tmp_path, db_url, run_id=run_row())
    assert results == {"t1": 0, "t2": "skipped"}
    assert run_row.read().status == "success"


def test_a_skipped_task_leaves_a_zero_length_skipped_row_in_its_run(tmp_path, db_url, run_row, outcomes):
    t2 = task("t2", depends_on=[{"task": "t1", "when": "failed"}])
    run_job(job(task("t1"), t2), tmp_path, db_url, run_id=run_row())
    [row] = rows(db_url, "t2")
    assert (row.job, row.status, row.duracao_segundos, row.run_id) == ("j", "skipped", 0.0, "run-1")
    assert row.inicio is not None
    assert row.fim == row.inicio


def test_a_skipped_parent_satisfies_a_when_skipped_edge(tmp_path, db_url, outcomes):
    t2 = task("t2", depends_on=[{"task": "t1", "when": "failed"}])
    t3 = task("t3", depends_on=[{"task": "t2", "when": "skipped"}])
    assert run_job(job(task("t1"), t2, t3), tmp_path, db_url) == {"t1": 0, "t2": "skipped", "t3": 0}


def test_an_edge_to_an_inactive_task_is_dropped(tmp_path, db_url, outcomes):
    """The inactive task is pruned with its edges, so its dependent runs as a root."""
    t1 = task("t1", active=False)
    t2 = task("t2", depends_on=["t1"])
    assert run_job(job(t1, t2), tmp_path, db_url) == {"t1": None, "t2": 0}


def test_a_local_run_gets_a_synthesized_run_id(tmp_path, db_url, outcomes):
    """Without a caller's run_id, the run's rows still share one, so handlers can filter by it."""
    t2 = task("t2", depends_on=[{"task": "t1", "when": "failed"}])
    run_job(job(task("t1"), t2), tmp_path, db_url)
    [row] = rows(db_url, "t2")
    assert re.fullmatch(r"local-[0-9a-f]{12}", row.run_id)


def test_retry_waits_between_attempts_but_not_after_the_last(tmp_path, db_url, monkeypatch):
    sleeps: list[float] = []
    calls: list[int] = []
    monkeypatch.setattr(engine.time, "sleep", sleeps.append)
    monkeypatch.setattr(engine, "run_task", lambda *_a, **_k: calls.append(1) or 7)
    t = Task(name="t", script="t.py", retry=RetryConfig(attempts=3, delay_seconds=5))
    assert run_task_with_retry(t, job(t), tmp_path, db_url) == 7
    assert len(calls) == 3
    assert sleeps == [5, 5]


# --- df-h864: a failure nobody takes over fails the run ------------------------------------------
# The run is failed when a task fails and no dependent with a `failed` condition on it ran (a
# failed leaf is the simplest case: it has no dependents at all), or when an error task failed.
# Skipped tasks stay neutral: they never fail the run themselves.


def test_a_failed_root_whose_dependents_are_all_skipped_fails_the_run(
        tmp_path, db_url, run_row, outcomes):
    """The 2026-11-12 incident: fetch fails, its whole chain is skipped, an independent root
    succeeds. Every leaf is skipped, yet nothing handled the failure."""
    outcomes["fetch"] = 1
    transform = task("transform", depends_on=["fetch"])
    report = task("report", depends_on=["transform", "calendar"])
    results = run_job(job(task("fetch"), task("calendar"), transform, report),
                      tmp_path, db_url, run_id=run_row())
    assert results == {"fetch": 1, "calendar": 0, "transform": "skipped", "report": "skipped"}
    assert run_row.read().status == "failed"


def test_a_failure_a_when_failed_dependent_takes_over_keeps_the_run_successful(
        tmp_path, db_url, run_row, outcomes):
    """The alert pattern (D10): the main chain is skipped, the handler runs and succeeds."""
    outcomes["fetch"] = 1
    transform = task("transform", depends_on=["fetch"])
    notify = task("notify", depends_on=[{"task": "fetch", "when": "failed"}])
    results = run_job(job(task("fetch"), transform, notify), tmp_path, db_url, run_id=run_row())
    assert results == {"fetch": 1, "transform": "skipped", "notify": 0}
    assert run_row.read().status == "success"


def test_a_when_completed_dependent_takes_over_a_failure(tmp_path, db_url, run_row, outcomes):
    """`completed` is [succeeded, failed]: a dependent that runs either way handles the failure."""
    outcomes["t1"] = 1
    t2 = task("t2", depends_on=[{"task": "t1", "when": "completed"}])
    t3 = task("t3", depends_on=["t2"])
    assert run_job(job(task("t1"), t2, t3), tmp_path, db_url, run_id=run_row()) == {
        "t1": 1, "t2": 0, "t3": 0}
    assert run_row.read().status == "success"


def test_a_when_failed_dependent_that_was_skipped_does_not_take_over(
        tmp_path, db_url, run_row, outcomes):
    """The handler also needs t0 to fail (match: all); t0 succeeded, so it never ran."""
    outcomes["t1"] = 1
    handler = task("handler", depends_on=[{"task": "t1", "when": "failed"},
                                          {"task": "t0", "when": "failed"}])
    results = run_job(job(task("t0"), task("t1"), handler), tmp_path, db_url, run_id=run_row())
    assert results == {"t0": 0, "t1": 1, "handler": "skipped"}
    assert run_row.read().status == "failed"


def test_a_when_skipped_dependent_does_not_take_over_a_failure(
        tmp_path, db_url, run_row, outcomes):
    outcomes["t1"] = 1
    t2 = task("t2", depends_on=["t1"])
    t3 = task("t3", depends_on=[{"task": "t2", "when": "skipped"}])
    assert run_job(job(task("t1"), t2, t3), tmp_path, db_url, run_id=run_row()) == {
        "t1": 1, "t2": "skipped", "t3": 0}
    assert run_row.read().status == "failed"


def test_a_handler_that_fails_in_turn_fails_the_run(tmp_path, db_url, run_row, outcomes):
    """The handler took over t1, but its own failure is not taken over by anyone."""
    outcomes.update(t1=1, handler=1)
    handler = task("handler", depends_on=[{"task": "t1", "when": "failed"}])
    after = task("after", depends_on=["handler"])
    assert run_job(job(task("t1"), handler, after), tmp_path, db_url, run_id=run_row()) == {
        "t1": 1, "handler": 1, "after": "skipped"}
    assert run_row.read().status == "failed"


def test_an_inactive_handler_does_not_take_over_a_failure(tmp_path, db_url, run_row, outcomes):
    outcomes["t1"] = 1
    handler = task("handler", depends_on=[{"task": "t1", "when": "failed"}], active=False)
    assert run_job(job(task("t1"), handler), tmp_path, db_url, run_id=run_row()) == {
        "t1": 1, "handler": None}
    assert run_row.read().status == "failed"


def test_run_status_reads_the_outcome_from_the_job_and_its_results():
    """The pure function the CLI uses for its exit code agrees with the recorded status."""
    t2 = task("t2", depends_on=["t1"])
    notify = task("notify", depends_on=[{"task": "t1", "when": "failed"}])
    assert engine.run_status(job(task("t1"), t2), {"t1": 1, "t2": "skipped"}) == "failed"
    assert engine.run_status(job(task("t1"), t2, notify),
                             {"t1": 1, "t2": "skipped", "notify": 0}) == "success"
    assert engine.run_status(job(task("t1"), t2), {"t1": 0, "t2": 0}) == "success"
