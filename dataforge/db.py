from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, text
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime


class Base(DeclarativeBase):
    pass


class ExecucaoORM(Base):
    __tablename__ = "execucoes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job = Column(String, nullable=False)
    task = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    inicio = Column(DateTime, nullable=False, default=datetime.utcnow)
    fim = Column(DateTime, nullable=True)
    duracao_segundos = Column(Float, nullable=True)
    output = Column(Text, nullable=True)
    run_id = Column(String, nullable=True)
    owner_id = Column(String, nullable=False, default="system")


class JobRunORM(Base):
    __tablename__ = "job_runs"

    id = Column(String, primary_key=True)
    workspace = Column(String, nullable=False)
    job = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    owner_id = Column(String, nullable=False, default="system")
    # "manual" for CLI/GUI runs, or the trigger name for OS-scheduled runs
    trigger = Column(String, nullable=True)


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


def init_db(db_url: str):
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)

    # Idempotent owner_id migration — SQLite has no ALTER TABLE ... ADD COLUMN IF NOT EXISTS
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(execucoes)"))
        columns = [row[1] for row in result.fetchall()]
        if "owner_id" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE execucoes ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'system'"
                )
            )
            conn.commit()

        result = conn.execute(text("PRAGMA table_info(execucoes)"))
        columns = [row[1] for row in result.fetchall()]
        if "output" not in columns:
            conn.execute(text("ALTER TABLE execucoes ADD COLUMN output TEXT"))
            conn.commit()

        result = conn.execute(text("PRAGMA table_info(execucoes)"))
        columns = [row[1] for row in result.fetchall()]
        if "run_id" not in columns:
            conn.execute(text("ALTER TABLE execucoes ADD COLUMN run_id TEXT"))
            conn.commit()

        result = conn.execute(text("PRAGMA table_info(job_runs)"))
        columns = [row[1] for row in result.fetchall()]
        if "trigger" not in columns:
            conn.execute(text("ALTER TABLE job_runs ADD COLUMN trigger TEXT"))
            conn.commit()

    return engine
