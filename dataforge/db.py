from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, text
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone
import threading

from .liveness import current_owner_text


def utcnow() -> datetime:
    """Current UTC time without tzinfo, the form every DateTime column here stores.

    Replaces the deprecated datetime.utcnow(); an aware value would not compare with the naive
    values already in the databases."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ExecucaoORM(Base):
    __tablename__ = "execucoes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job = Column(String, nullable=False)
    task = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    inicio = Column(DateTime, nullable=False, default=utcnow)
    fim = Column(DateTime, nullable=True)
    duracao_segundos = Column(Float, nullable=True)
    output = Column(Text, nullable=True)
    run_id = Column(String, nullable=True)
    owner_id = Column(String, nullable=False, default="system")
    # The process that wrote the row (dataforge.liveness.Owner as JSON); NULL on older rows.
    owner_process = Column(Text, nullable=True, default=current_owner_text)


class JobRunORM(Base):
    __tablename__ = "job_runs"

    id = Column(String, primary_key=True)
    workspace = Column(String, nullable=False)
    job = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=False, default=utcnow)
    finished_at = Column(DateTime, nullable=True)
    owner_id = Column(String, nullable=False, default="system")
    # "manual" for CLI/GUI runs, or the trigger name for OS-scheduled runs
    trigger = Column(String, nullable=True)
    # The process that runs it (dataforge.liveness.Owner as JSON); NULL on older rows.
    owner_process = Column(Text, nullable=True, default=current_owner_text)
    # Why the run ended as it did, when the status alone does not say (an interrupted run).
    reason = Column(Text, nullable=True)


class ScheduleORM(Base):
    __tablename__ = "schedules"

    id          = Column(String, primary_key=True)  # "{workspace}:{job}"
    workspace   = Column(String, nullable=False)
    job         = Column(String, nullable=False)
    cron        = Column(String, nullable=False)
    enabled     = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    owner_id    = Column(String, nullable=False, default="system")


# create_all and the column migrations below check, then create: two threads initialising the same
# new database (two jobs of one trigger, two API runs) both see the table missing and the second
# CREATE fails. One lock per process serialises them (df-h864).
_SCHEMA_LOCK = threading.Lock()


# Columns added after a table was first released, in the order they came: init_db adds each one an
# older database lacks (SQLite has no ALTER TABLE ... ADD COLUMN IF NOT EXISTS). Whole statements,
# not formatted ones: nothing here is ever built from a value.
_ADDED_COLUMNS = {
    "PRAGMA table_info(execucoes)": [
        ("owner_id", "ALTER TABLE execucoes ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'system'"),
        ("output", "ALTER TABLE execucoes ADD COLUMN output TEXT"),
        ("run_id", "ALTER TABLE execucoes ADD COLUMN run_id TEXT"),
        ("owner_process", "ALTER TABLE execucoes ADD COLUMN owner_process TEXT"),
    ],
    "PRAGMA table_info(job_runs)": [
        ("trigger", "ALTER TABLE job_runs ADD COLUMN trigger TEXT"),
        ("owner_process", "ALTER TABLE job_runs ADD COLUMN owner_process TEXT"),
        ("reason", "ALTER TABLE job_runs ADD COLUMN reason TEXT"),
    ],
}


def init_db(db_url: str):
    engine = create_engine(db_url)
    with _SCHEMA_LOCK:
        _create_schema(engine)
    return engine


def _create_schema(engine) -> None:
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        for table_info, added in _ADDED_COLUMNS.items():
            present = {row[1] for row in conn.execute(text(table_info))}
            for name, alter in added:
                if name not in present:
                    conn.execute(text(alter))
                    conn.commit()
