"""How finance_daily, reclassificar and extrato end when a task fails or the data checks warn (ADR 0004,
df-ahua.3). The engine decides a run's status from its leaves and its error tasks only, so these
tests run the real job.yaml through the real engine with a fake task runner: each scenario states
which tasks fail, and the run status and the trip-wire task are checked.

The jobs are read from this checkout (_delta_util.ROOT).
"""
import re

import pytest
from sqlalchemy.orm import Session

from dataforge import engine
from dataforge.db import JobRunORM
from dataforge.engine import run_job, run_task_with_retry
from dataforge.models import RetryConfig
from dataforge.parser import load_job
from tests import _delta_util
from tests.test_report_scripts import ENTRADAS

WORKSPACE = _delta_util.SCRIPTS.parent
NO_RETRY = RetryConfig(attempts=1, delay_seconds=0)


@pytest.fixture
def outcomes(monkeypatch):
    """Task name -> return code for the fake runner; unlisted tasks succeed. `error` tasks run for
    real, since their handler is what forces the run to fail."""
    codes: dict[str, int] = {}
    monkeypatch.setattr(engine, "run_task_with_retry",
                        lambda t, *a, **k: run_task_with_retry(t, *a, **k) if t.type == "error"
                        else codes.get(t.name, 0))
    return codes


def _run(job_name, tmp_path, db_url, global_engine, activate=()):
    job = load_job(WORKSPACE, job_name)
    for t in job.tasks:
        t.retry = NO_RETRY
        if t.name in activate:
            t.active = True
    with Session(global_engine) as s:
        s.add(JobRunORM(id="run-1", workspace="ws", job=job_name, status="pending"))
        s.commit()
    results = run_job(job, tmp_path, db_url, "run-1")
    with Session(global_engine) as s:
        return s.get(JobRunORM, "run-1").status, results


def test_finance_daily_clean_run_succeeds_and_the_trip_wire_stays_off(tmp_path, db_url,
                                                                       global_engine, outcomes):
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "success"
    assert results["data_warnings"] == 0
    assert results["job_failed"] == "skipped"


# The bug the trip-wire fixes: before it, a failed fetch (all else skipped) ended as success.
@pytest.mark.parametrize("failing", ["fetch", "transform", "curate", "fact_card_statement",
                                     "classify", "report", "conferencia_saldo", "lancamentos_suspeitos",
                                     "extrato", "veredito_suspeitos", "conferencia_fatura", "calendar"])
def test_finance_daily_fails_when_any_task_fails(tmp_path, db_url, global_engine, outcomes, failing):
    outcomes[failing] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["job_failed"] == 1


# A failed balance check does not hide the other tasks' warnings: data_warnings still runs, and the
# trip-wire still fails the run (df-z4fh.1).
def test_finance_daily_warnings_run_when_the_balance_check_fails(tmp_path, db_url, global_engine, outcomes):
    outcomes["conferencia_saldo"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["lancamentos_suspeitos"] == "skipped"
    assert results["extrato"] == 0   # the statement does not depend on the balance check (df-z4fh.5)
    assert results["data_warnings"] == 0
    assert results["job_failed"] == 1


# Nor does a failure of the suspect marking (df-z4fh.2).
def test_finance_daily_warnings_run_when_the_suspect_marking_fails(tmp_path, db_url, global_engine, outcomes):
    outcomes["lancamentos_suspeitos"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["extrato"] == 0
    assert results["veredito_suspeitos"] == "skipped"   # its table would be yesterday's (df-z4fh.3)
    assert results["data_warnings"] == 0
    assert results["job_failed"] == 1


# Nor does a failure of the statement reading (df-z4fh.5): a PDF it cannot read fails the run, after the
# warnings ran.
def test_finance_daily_warnings_run_when_the_statement_reading_fails(tmp_path, db_url, global_engine, outcomes):
    outcomes["extrato"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["veredito_suspeitos"] == 0   # it reads the statement tables extrato left (df-z4fh.3)
    assert results["data_warnings"] == 0
    assert results["job_failed"] == 1


# Nor does a failure of the statement's verdict (df-z4fh.3).
def test_finance_daily_warnings_run_when_the_verdict_fails(tmp_path, db_url, global_engine, outcomes):
    outcomes["veredito_suspeitos"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["data_warnings"] == 0
    assert results["job_failed"] == 1


# Nor does a failure of the card check by each bill (df-acz4).
def test_finance_daily_warnings_run_when_the_card_check_fails(tmp_path, db_url, global_engine, outcomes):
    outcomes["conferencia_fatura"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["data_warnings"] == 0
    assert results["job_failed"] == 1


# The card check needs the statements: without fact_card_statement it is skipped, and the warnings still run
# (df-acz4).
def test_finance_daily_card_check_is_skipped_without_the_statements(tmp_path, db_url, global_engine, outcomes):
    outcomes["fact_card_statement"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "failed"
    assert results["conferencia_fatura"] == "skipped"
    assert results["job_failed"] == 1


# extrato reads the statements once: a retry would read the same files (df-z4fh.5).
def test_extrato_runs_once(monkeypatch, tmp_path):
    job = load_job(WORKSPACE, "finance_daily")
    task = next(t for t in job.tasks if t.name == "extrato")
    calls = []
    monkeypatch.setattr(engine, "run_task", lambda *a, **k: calls.append(a[0].name) or 1)
    assert run_task_with_retry(task, job, tmp_path, "sqlite://") == 1
    assert calls == ["extrato"]


# A suspicious finding fails data_warnings but not the run: the data was written.
# Not so on the current engine: since its PR #49 (df-h864) a failure is absorbed only by a dependent
# that ran with an edge accepting it, and alert_warnings, the one that has it, is inactive. Owner's
# decision pending (df-vlfl): accept the failed run, or change the wiring. strict: it must flip.
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-vlfl: with alert_warnings inactive, the current engine fails the run on a data warning")
def test_finance_daily_warning_does_not_fail_the_run(tmp_path, db_url, global_engine, outcomes):
    outcomes["data_warnings"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine)
    assert status == "success"
    assert results["job_failed"] == "skipped"


def test_the_pending_warning_test_expects_only_its_assertion_to_fail():
    """A fixture, import or engine error in it must fail the suite, not pass as the expected failure."""
    (mark,) = test_finance_daily_warning_does_not_fail_the_run.pytestmark
    assert (mark.name, mark.kwargs["strict"], mark.kwargs["raises"]) == ("xfail", True, AssertionError)


# Once the owner activates the alerts (df-ahua.3.2): a warning sends alert_warnings, and a failure
# sends alert_failure and still fails the run.
def test_finance_daily_alerts_once_activated(tmp_path, db_url, global_engine, outcomes):
    outcomes["data_warnings"] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine,
                           activate=("alert_warnings", "alert_failure"))
    assert status == "success"
    assert results["alert_warnings"] == 0
    assert results["alert_failure"] == "skipped"


@pytest.mark.parametrize("failing", ["transform", "conferencia_saldo", "lancamentos_suspeitos", "extrato",
                                     "veredito_suspeitos", "conferencia_fatura"])
def test_finance_daily_failure_alerts_and_fails_once_activated(tmp_path, db_url, global_engine,
                                                               outcomes, failing):
    outcomes[failing] = 1
    status, results = _run("finance_daily", tmp_path, db_url, global_engine,
                           activate=("alert_warnings", "alert_failure"))
    assert status == "failed"
    assert results["alert_failure"] == 0
    assert results["alert_warnings"] == "skipped"


# data_warnings answers from the findings this run recorded: a retry repeats the same answer
# (df-ahua.3.4), so it runs once. The job-level retry does not reach tasks (DataForge df-a16k), so
# the task carries its own.
def test_data_warnings_runs_once(monkeypatch, tmp_path):
    job = load_job(WORKSPACE, "finance_daily")
    task = next(t for t in job.tasks if t.name == "data_warnings")
    calls = []
    monkeypatch.setattr(engine, "run_task", lambda *a, **k: calls.append(a[0].name) or 1)
    assert run_task_with_retry(task, job, tmp_path, "sqlite://") == 1
    assert calls == ["data_warnings"]


# fetch is the task that needs the network: at the 06:00 trigger the machine may still be without
# DNS, and the engine default (3 attempts 30 s apart) is spent in about a minute (DataForge df-muzy).
# Its own retry waits up to 30 min; the job-level one does not reach it (DataForge df-a16k).
def test_fetch_waits_up_to_thirty_minutes_for_the_network(monkeypatch, tmp_path):
    job = load_job(WORKSPACE, "finance_daily")
    task = next(t for t in job.tasks if t.name == "fetch")
    calls, waits = [], []
    monkeypatch.setattr(engine, "run_task", lambda *a, **k: calls.append(a[0].name) or 1)
    monkeypatch.setattr(engine.time, "sleep", waits.append)
    assert run_task_with_retry(task, job, tmp_path, "sqlite://") == 1
    assert calls == ["fetch"] * 7
    assert waits == [300] * 6


def test_alert_tasks_ship_inactive_and_without_recipients():
    job = load_job(WORKSPACE, "finance_daily")
    alerts = [t for t in job.tasks if t.type == "message"]
    assert {t.name for t in alerts} == {"alert_warnings", "alert_failure"}
    for t in alerts:
        assert t.active is False
        assert t.params["recipients"] == {}


@pytest.mark.parametrize("case", [(None, "success"), ("classify", "failed"), ("report", "failed"),
                                  ("conferencia_saldo", "failed"), ("lancamentos_suspeitos", "failed")])
def test_reclassificar_fails_when_a_task_fails(tmp_path, db_url, global_engine, outcomes, case):
    failing, expected = case
    if failing:
        outcomes[failing] = 1
    status, _ = _run("reclassificar", tmp_path, db_url, global_engine)
    assert status == expected


# The statement job (df-7xk9.1): the verdict still runs over what a failed statement task left, and the
# run fails all the same.
@pytest.mark.parametrize("case", [(None, "success", 0), ("extrato", "failed", 0),
                                  ("veredito_suspeitos", "failed", 1)])
def test_extrato_job_fails_when_a_task_fails(tmp_path, db_url, global_engine, outcomes, case):
    failing, expected, veredito = case
    if failing:
        outcomes[failing] = 1
    status, results = _run("extrato", tmp_path, db_url, global_engine)
    assert status == expected
    assert results["veredito_suspeitos"] == veredito


def test_extrato_job_runs_on_demand_and_never_calls_the_bank():
    job = load_job(WORKSPACE, "extrato")
    assert [t.name for t in job.tasks] == ["extrato", "veredito_suspeitos", "job_failed"]
    assert job.schedule is None
    finance_daily = {t.name: t for t in load_job(WORKSPACE, "finance_daily").tasks}
    for t in job.tasks[:2]:
        assert (t.script, t.params) == (finance_daily[t.name].script, finance_daily[t.name].params)


def test_report_scripts_tests_seed_every_table_the_report_tasks_read():
    """test_report_scripts.py copies the real curated into a temp folder and only runs where that data
    exists, so CI never sees it break. A table the report tasks start reading has to be in its list
    (df-8rbr: dim_investment was not, and four tests failed only on the real data)."""
    scripts = [(WORKSPACE / "scripts" / "report" / f"{name}.py").read_text(encoding="utf-8")
               for name in ("classify", "report_curated")]
    read = {t for s in scripts for t in re.findall(r'read_delta_table\(args\.source, "(\w+)"\)', s)}
    written = {t for s in scripts for t in re.findall(r'write_delta_overwrite\(args\.target, "(\w+)"', s)}
    assert read - written == set(ENTRADAS)
