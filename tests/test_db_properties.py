"""Rules of dataforge/db.py: init_db brings any older run-history database to the current schema
without losing rows (docs/subsystems/db.md), and rows get the documented defaults."""
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import example, given, strategies as st
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, JobRunORM, ScheduleORM, init_db, utcnow
from dataforge.liveness import current_owner_text

# Columns init_db adds to a database created before they existed (docs/subsystems/db.md).
_MIGRATED = {"execucoes": ["owner_id", "output", "run_id", "owner_process"],
             "job_runs": ["trigger", "owner_process", "reason"]}
# The execucoes / job_runs tables as the oldest databases have them: the current columns minus
# those added later.
_LEGACY_EXECUCOES = """CREATE TABLE execucoes (
    id INTEGER NOT NULL PRIMARY KEY, job VARCHAR NOT NULL, task VARCHAR NOT NULL,
    status VARCHAR NOT NULL, inicio DATETIME NOT NULL, fim DATETIME, duracao_segundos FLOAT{extra})"""
_LEGACY_JOB_RUNS = """CREATE TABLE job_runs (
    id VARCHAR NOT NULL PRIMARY KEY, workspace VARCHAR NOT NULL, job VARCHAR NOT NULL,
    status VARCHAR NOT NULL, started_at DATETIME NOT NULL, finished_at DATETIME,
    owner_id VARCHAR NOT NULL{extra})"""
_LEGACY_TYPES = {"owner_id": "VARCHAR NOT NULL DEFAULT 'system'", "output": "TEXT", "run_id": "VARCHAR",
                 "trigger": "VARCHAR", "owner_process": "TEXT", "reason": "TEXT"}


def _columns(engine, table: str) -> set[str]:
    """Column names, case-folded: SQLite matches identifiers without regard to case."""
    return {c["name"].casefold() for c in inspect(engine).get_columns(table)}


def _fresh_columns() -> dict[str, set[str]]:
    with tempfile.TemporaryDirectory() as d:
        engine = init_db(make_db_url(Path(d) / "fresh.db"))
        try:
            return {t: _columns(engine, t) for t in ("execucoes", "job_runs", "schedules")}
        finally:
            engine.dispose()


_FRESH = _fresh_columns()


def _subsets(cols):
    return st.sets(st.sampled_from(cols)) if cols else st.just(set())


@given(_subsets(_MIGRATED["execucoes"]), _subsets(_MIGRATED["job_runs"]))
@example(set(_MIGRATED["execucoes"]), set(_MIGRATED["job_runs"]))
@example(set(), set())
@example({"output"}, set())
def test_init_db_brings_an_older_database_to_the_current_schema(missing_exec, missing_runs):
    """A database written before any of the added columns existed ends up, after init_db (and again
    on the next start), with exactly the columns of a new database; the rows it held survive, and old
    run rows are owned by 'system'."""
    with tempfile.TemporaryDirectory() as d:
        url = make_db_url(Path(d) / "old.db")
        old = create_engine(url)
        exec_extra = "".join(f", {c} {_LEGACY_TYPES[c]}" for c in _MIGRATED["execucoes"] if c not in missing_exec)
        runs_extra = "".join(f", {c} {_LEGACY_TYPES[c]}" for c in _MIGRATED["job_runs"] if c not in missing_runs)
        with old.begin() as conn:
            conn.execute(text(_LEGACY_EXECUCOES.format(extra=exec_extra)))
            conn.execute(text(_LEGACY_JOB_RUNS.format(extra=runs_extra)))
            conn.execute(text("INSERT INTO execucoes (job, task, status, inicio) "
                              "VALUES ('j', 't', 'success', '2026-01-01 00:00:00')"))
            conn.execute(text("INSERT INTO job_runs (id, workspace, job, status, started_at, owner_id) "
                              "VALUES ('r1', 'ws', 'j', 'success', '2026-01-01 00:00:00', 'system')"))
        old.dispose()

        init_db(url).dispose()
        engine = init_db(url)   # every CLI/API start runs it again on the migrated database
        try:
            for table, cols in _FRESH.items():
                assert _columns(engine, table) == cols, table
            with Session(engine) as s:
                (row,) = s.query(ExecucaoORM).all()
                assert (row.job, row.task, row.status, row.owner_id) == ("j", "t", "success", "system")
                assert row.owner_process is None   # an old row has no recorded owner
                (run,) = s.query(JobRunORM).all()
                assert (run.id, run.workspace, run.trigger, run.owner_process, run.reason) == (
                    "r1", "ws", None, None, None)
                s.add(ExecucaoORM(job="j", task="t2", output="out", run_id="r1", owner_id="u"))
                s.add(JobRunORM(id="r2", workspace="ws", job="j", trigger="nightly", reason="why"))
                s.commit()
                new = s.query(ExecucaoORM).filter_by(task="t2").one()
                assert (new.output, new.run_id, new.owner_id) == ("out", "r1", "u")
                assert new.owner_process == current_owner_text()
                r2 = s.get(JobRunORM, "r2")
                assert (r2.trigger, r2.reason, r2.owner_process) == ("nightly", "why", current_owner_text())
        finally:
            engine.dispose()


def test_init_db_on_a_current_database_changes_nothing(db_url):
    """Running init_db again (every CLI/API start does) keeps the schema and the rows."""
    engine = init_db(db_url)
    with Session(engine) as s:
        s.add(ExecucaoORM(job="j", task="t"))
        s.commit()
    engine.dispose()
    engine = init_db(db_url)
    try:
        assert {t: _columns(engine, t) for t in _FRESH} == _FRESH
        with Session(engine) as s:
            assert s.query(ExecucaoORM).count() == 1
    finally:
        engine.dispose()


def test_init_db_returns_an_engine_on_that_database(tmp_path):
    """The engine init_db returns writes to the file the URL names."""
    path = tmp_path / "h.db"
    engine = init_db(make_db_url(path))
    try:
        assert engine.url.database == path.resolve().as_posix()
        assert path.is_file()
    finally:
        engine.dispose()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset (switching the local zone) is Unix-only")
def test_utcnow_is_naive_utc_whatever_the_local_timezone():
    """utcnow is the UTC time without tzinfo, the form every DateTime column stores, also on a machine
    set to Brazil time (production); an aware value would not compare with the naive ones stored."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TZ", "Etc/GMT+3")
        time.tzset()
        try:
            before = datetime.now(timezone.utc).replace(tzinfo=None)
            now = utcnow()
            after = datetime.now(timezone.utc).replace(tzinfo=None)
        finally:
            mp.undo()
            time.tzset()
    assert now.tzinfo is None
    assert before <= now <= after


def test_a_new_row_gets_the_documented_defaults(db_url):
    """A row saved with only what identifies it is 'pending', owned by 'system', started now (naive
    UTC), and a schedule starts enabled (docs/subsystems/db.md)."""
    engine = init_db(db_url)
    before = utcnow() - timedelta(seconds=1)
    with Session(engine) as s:
        s.add(ExecucaoORM(job="j", task="t"))
        s.add(JobRunORM(id="r", workspace="ws", job="j"))
        s.add(ScheduleORM(id="ws:j", workspace="ws", job="j", cron="0 2 * * *"))
        s.commit()
        e = s.query(ExecucaoORM).one()
        r = s.get(JobRunORM, "r")
        sc = s.get(ScheduleORM, "ws:j")
        after = utcnow() + timedelta(seconds=1)
        assert (e.status, e.owner_id, e.fim, e.output, e.run_id) == ("pending", "system", None, None, None)
        assert before <= e.inicio <= after
        assert (r.status, r.owner_id, r.trigger, r.finished_at, r.reason) == (
            "pending", "system", None, None, None)
        # Both rows record the process that wrote them, so a dead one can be told (df-bw7).
        assert e.owner_process == r.owner_process == current_owner_text()
        assert before <= r.started_at <= after
        assert (sc.enabled, sc.owner_id, sc.last_run_at, sc.next_run_at) == (True, "system", None, None)
    engine.dispose()
