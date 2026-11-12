from sqlalchemy import text
from sqlalchemy.orm import Session
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
