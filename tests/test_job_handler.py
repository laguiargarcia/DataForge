"""Rules of dataforge/tasks/job_handler.py, the `job` task: it runs the job named in `job:` from the
same workspace, inside the caller's run (its task rows carry the caller's run id), and returns 0
when that job's run status is success, else 1. A job task without `job:` is refused."""
import pytest
from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, init_db
from dataforge.models import Job, RetryConfig, Task
from dataforge.tasks.job_handler import JobHandler

_NO_RETRY = RetryConfig(attempts=1, delay_seconds=0)
_STEP = "retry: {attempts: 1, delay_seconds: 0}\n"


def _plant_job(ws, name: str, tasks_yaml: str) -> None:
    job_dir = ws / "jobs" / name
    job_dir.mkdir(parents=True)
    (job_dir / "job.yaml").write_bytes(f"name: {name}\n{_STEP}tasks:\n{tasks_yaml}".encode("utf-8"))


def _call(ws, child: str, run_id="parent-run"):
    url = make_db_url(ws / "dataforge.db")
    task = Task(name="call", type="job", job=child, retry=_NO_RETRY)
    parent = Job(name="parent", tasks=[task], retry=_NO_RETRY)
    return JobHandler().run(task, parent, ws, url, run_id=run_id), url


def _rows(url: str) -> list[tuple]:
    engine = init_db(url)
    with Session(engine) as s:
        rows = [(r.job, r.task, r.status, r.run_id) for r in s.query(ExecucaoORM)]
    engine.dispose()
    return rows


def test_a_called_job_that_succeeds_returns_0_inside_the_caller_s_run(tmp_path, dataforge_home):
    """A job task whose job succeeds returns 0, and that job's task rows carry the caller's run id."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ok.py").write_bytes(b"print('ok')\n")
    _plant_job(tmp_path, "child", f"  - {{name: step, script: ok.py, {_STEP.strip()}}}\n")
    rc, url = _call(tmp_path, "child")
    assert rc == 0
    assert _rows(url) == [("child", "step", "success", "parent-run")]


def test_a_called_job_that_fails_returns_1(tmp_path, dataforge_home):
    """A job task whose job's run status is failed returns 1."""
    _plant_job(tmp_path, "child", f"  - {{name: stop, type: error, {_STEP.strip()}}}\n")
    rc, url = _call(tmp_path, "child")
    assert rc == 1
    assert _rows(url) == [("child", "stop", "failed", "parent-run")]


def test_a_job_task_without_a_job_is_refused_naming_the_task(tmp_path):
    """A job task with no `job:` raises ValueError naming the task, before running anything."""
    task = Task(name="dangling", type="job", retry=_NO_RETRY)
    with pytest.raises(ValueError, match="'dangling'"):
        JobHandler().run(task, Job(name="parent", tasks=[task]), tmp_path,
                         make_db_url(tmp_path / "dataforge.db"), run_id="r1")
    assert not (tmp_path / "dataforge.db").exists()
