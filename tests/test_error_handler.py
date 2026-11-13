"""Rules of dataforge/tasks/error_handler.py: an `error` task always fails and records exactly one
finished, zero-length `failed` row in the task store, carrying the run id and its message."""
import tempfile
from pathlib import Path

from hypothesis import example, given, settings, strategies as st
from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, init_db, utcnow
from dataforge.models import Job, RetryConfig, Task
from dataforge.tasks.error_handler import ErrorHandler

_NO_RETRY = RetryConfig(attempts=1, delay_seconds=0)


def _rows(url: str) -> list[ExecucaoORM]:
    engine = init_db(url)
    with Session(engine, expire_on_commit=False) as s:
        rows = s.query(ExecucaoORM).all()
        s.expunge_all()
    engine.dispose()
    return rows


def test_error_handler_always_fails_and_writes_row(tmp_path):
    url = make_db_url(tmp_path / "dataforge.db")
    init_db(url)
    task = Task(name="boom", type="error", params={"message": "explicit fail"})
    job = Job(name="j", tasks=[task], retry=_NO_RETRY)
    rc = ErrorHandler().run(task, job, tmp_path, url, run_id=None)
    assert rc == 1
    eng = init_db(url)
    with Session(eng) as s:
        row = s.query(ExecucaoORM).filter_by(task="boom").one()
        assert row.status == "failed"
        assert "explicit fail" in (row.output or "")


def test_error_handler_forces_run_failure_flag():
    assert ErrorHandler.forces_run_failure is True


@settings(max_examples=20)
@given(message=st.none() | st.text(min_size=1, max_size=30),
       run_id=st.none() | st.from_regex(r"[a-z0-9-]{1,12}", fullmatch=True))
@example(message=None, run_id="r-1")
@example(message="disk full", run_id=None)
def test_an_error_task_records_one_finished_failed_row_of_zero_length(message, run_id):
    """Whatever its message and run id, an error task returns 1 and leaves one `failed` row for its
    job and task, carrying that run id, that started and finished at the same instant (duration 0)
    inside the call, and whose output is the task's message, or a non-empty default without one."""
    params = {} if message is None else {"message": message}
    task = Task(name="boom", type="error", params=params)
    job = Job(name="nightly", tasks=[task], retry=_NO_RETRY)
    # The handler's engine keeps the database open, which Windows will not delete (GC is off in tests).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        url = make_db_url(Path(d) / "dataforge.db")
        before = utcnow()
        rc = ErrorHandler().run(task, job, Path(d), url, run_id=run_id)
        after = utcnow()
        rows = _rows(url)
    assert rc == 1
    assert len(rows) == 1
    row = rows[0]
    assert (row.job, row.task, row.status, row.run_id) == ("nightly", "boom", "failed", run_id)
    assert row.inicio is not None
    assert row.fim == row.inicio
    assert before <= row.inicio <= after
    assert row.duracao_segundos == 0.0
    if message is None:
        assert row.output
    else:
        assert row.output == message
