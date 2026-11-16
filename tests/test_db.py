import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
import dataforge.db as db
from dataforge.db import init_db, ExecucaoORM


def test_owner_id_added(db_url):
    """init_db adds owner_id column to execucoes table."""
    engine = init_db(db_url)
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(execucoes)"))
        columns = [row[1] for row in result.fetchall()]
    assert "owner_id" in columns


def test_init_db_idempotent(db_url):
    """Calling init_db twice on the same database must not raise."""
    init_db(db_url)
    init_db(db_url)  # must not raise OperationalError or any other exception


def test_owner_id_default_is_system(db_url):
    """New rows without explicit owner_id get 'system' as default."""
    engine = init_db(db_url)
    with Session(engine) as session:
        row = ExecucaoORM(job="j", task="t", status="pending")
        session.add(row)
        session.commit()
        session.refresh(row)
    assert row.owner_id == "system"


def test_owner_id_column_present_in_orm(db_url):
    """ExecucaoORM ORM model exposes owner_id attribute."""
    engine = init_db(db_url)
    with Session(engine) as session:
        row = ExecucaoORM(job="j2", task="t2", status="running", owner_id="user-abc")
        session.add(row)
        session.commit()
        session.refresh(row)
    assert row.owner_id == "user-abc"


def test_concurrent_init_db_on_a_fresh_database_does_not_race(tmp_path):
    """Two jobs of one trigger (or two API runs) start in threads and both create the schema of
    the same new workspace DB. create_all checks then creates, so without a lock one thread hits
    'table ... already exists' and its job never runs (df-h864, seen in CI)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from dataforge.compat import db_url as make_db_url

    threads = 8
    for attempt in range(10):
        url = make_db_url(tmp_path / f"fresh-{attempt}.db")
        barrier = threading.Barrier(threads)

        def init(_, barrier=barrier, url=url):
            barrier.wait()
            return init_db(url)

        with ThreadPoolExecutor(threads) as ex:
            list(ex.map(init, range(threads)))   # re-raises the first thread's exception


def test_init_db_survives_another_process_creating_the_schema_meanwhile(db_url, monkeypatch):
    """Another process creates the tables between create_all's check and its CREATE (df-n8ac.23):
    init_db checks again instead of failing with 'table already exists'."""

    path = make_url(db_url).database
    original = db.Base.metadata.create_all
    raced = []

    def create_all_after_the_other_process(engine, **kw):
        if not raced:
            raced.append(True)
            other = sqlite3.connect(path)   # the other process wins the race
            other.execute("CREATE TABLE job_runs (id VARCHAR NOT NULL PRIMARY KEY)")
            other.commit()
            other.close()
            return original(engine, checkfirst=False)   # our CREATE, from a stale check
        return original(engine, **kw)

    monkeypatch.setattr(db.Base.metadata, "create_all", create_all_after_the_other_process)
    engine = init_db(db_url)
    try:
        with engine.connect() as conn:
            tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        engine.dispose()
    assert raced == [True]
    assert {"job_runs", "execucoes"} <= tables


def test_init_db_does_not_retry_or_hide_other_operational_errors(db_url, monkeypatch):
    calls = []

    def broken(engine, **kw):
        calls.append(1)
        raise OperationalError("CREATE TABLE x", {}, Exception("disk I/O error"))

    monkeypatch.setattr(db.Base.metadata, "create_all", broken)
    with pytest.raises(OperationalError, match="disk I/O error"):
        init_db(db_url)
    assert len(calls) == 1


def test_init_db_gives_up_when_the_race_never_settles(db_url, monkeypatch):

    calls = []

    def always_raced(engine, **kw):
        calls.append(1)
        raise OperationalError("CREATE TABLE job_runs", {}, Exception("table job_runs already exists"))

    monkeypatch.setattr(db.Base.metadata, "create_all", always_raced)
    with pytest.raises(OperationalError, match="already exists"):
        init_db(db_url)
    assert len(calls) == db._SCHEMA_ATTEMPTS
