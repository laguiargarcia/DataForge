from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, init_db
from dataforge.models import Job, RetryConfig, Task
from dataforge.tasks.error_handler import ErrorHandler


def test_error_handler_always_fails_and_writes_row(tmp_path):
    url = make_db_url(tmp_path / "dataforge.db")
    init_db(url)
    task = Task(name="boom", type="error", params={"message": "explicit fail"})
    job = Job(name="j", tasks=[task], retry=RetryConfig(attempts=1, delay_seconds=0))
    rc = ErrorHandler().run(task, job, tmp_path, url, run_id=None)
    assert rc == 1
    eng = init_db(url)
    with Session(eng) as s:
        row = s.query(ExecucaoORM).filter_by(task="boom").one()
        assert row.status == "failed"
        assert "explicit fail" in (row.output or "")


def test_error_handler_forces_run_failure_flag():
    assert ErrorHandler.forces_run_failure is True
