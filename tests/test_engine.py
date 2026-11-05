import pytest
from pathlib import Path
from dataforge.engine import run_task_with_retry, run_job
from dataforge.models import Task, Job, RetryConfig


def make_task(name, script_name, depends_on=None, active=True, attempts=1):
    return Task(
        name=name,
        script=script_name,
        depends_on=depends_on or [],
        active=active,
        retry=RetryConfig(attempts=attempts, delay_seconds=0),
    )


def write_script(directory: Path, name: str, content: str) -> str:
    """Write a script under <directory>/scripts/ and return the bare relative path.

    scripts/ is the enforced root: task.script is bare and the engine resolves it at
    workspace_dir/scripts/<bare>, so the file must live under scripts/.
    """
    scripts_dir = directory / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / name).write_text(content)
    return name


def test_successful_task(tmp_path, db_url):
    write_script(tmp_path, "ok.py", "print('ok')\n")
    (tmp_path / "logs").mkdir()
    task = make_task("t1", "ok.py")
    job = Job(name="test_job", tasks=[task], retry=RetryConfig(attempts=1, delay_seconds=0))
    rc = run_task_with_retry(task, job, tmp_path, db_url)
    assert rc == 0


def test_retry_exhaustion(tmp_path, db_url):
    write_script(tmp_path, "fail.py", "import sys; sys.exit(1)\n")
    (tmp_path / "logs").mkdir()
    task = make_task("fail_task", "fail.py", attempts=2)
    job = Job(name="test_job", tasks=[task], retry=RetryConfig(attempts=1, delay_seconds=0))
    rc = run_task_with_retry(task, job, tmp_path, db_url)
    assert rc != 0


def test_downstream_skipped_when_parent_fails(tmp_path, db_url):
    write_script(tmp_path, "fail.py", "import sys; sys.exit(1)\n")
    write_script(tmp_path, "ok.py", "print('ok')\n")
    (tmp_path / "logs").mkdir()
    t1 = make_task("t1", "fail.py")
    t2 = make_task("t2", "ok.py", depends_on=["t1"])
    job = Job(name="j", tasks=[t1, t2], retry=RetryConfig(attempts=1, delay_seconds=0))
    results = run_job(job, tmp_path, db_url)
    assert results["t1"] != 0
    assert results["t2"] == "skipped"


def test_when_failed_triggers_handler(tmp_path, db_url):
    write_script(tmp_path, "fail.py", "import sys; sys.exit(1)\n")
    write_script(tmp_path, "ok.py", "print('ok')\n")
    (tmp_path / "logs").mkdir()
    t1 = make_task("t1", "fail.py")
    t2 = Task(name="t2", script="ok.py",
              depends_on=[{"task": "t1", "when": "failed"}],
              retry=RetryConfig(attempts=1, delay_seconds=0))
    job = Job(name="j", tasks=[t1, t2], retry=RetryConfig(attempts=1, delay_seconds=0))
    results = run_job(job, tmp_path, db_url)
    assert results["t2"] == 0


def test_match_any_fan_in(tmp_path, db_url):
    write_script(tmp_path, "ok.py", "print('ok')\n")
    write_script(tmp_path, "fail.py", "import sys; sys.exit(1)\n")
    (tmp_path / "logs").mkdir()
    t1 = make_task("t1", "ok.py")
    t2 = make_task("t2", "fail.py")
    t3 = Task(name="t3", script="ok.py",
              depends_on=[{"task": "t1", "when": "failed"},
                          {"task": "t2", "when": "failed"}],
              match="any",
              retry=RetryConfig(attempts=1, delay_seconds=0))
    job = Job(name="j", tasks=[t1, t2, t3], retry=RetryConfig(attempts=1, delay_seconds=0))
    results = run_job(job, tmp_path, db_url)
    assert results["t3"] == 0


def test_leaf_only_outcome_non_leaf_failure_is_success(tmp_path, db_url, global_engine):
    from dataforge.db import JobRunORM
    from sqlalchemy.orm import Session
    write_script(tmp_path, "fail.py", "import sys; sys.exit(1)\n")
    write_script(tmp_path, "ok.py", "print('ok')\n")
    (tmp_path / "logs").mkdir()
    t1 = make_task("t1", "fail.py")
    t2 = Task(name="t2", script="ok.py",
              depends_on=[{"task": "t1", "when": "failed"}],
              retry=RetryConfig(attempts=1, delay_seconds=0))
    job = Job(name="j", tasks=[t1, t2], retry=RetryConfig(attempts=1, delay_seconds=0))
    run_id = "test-run-1"
    with Session(global_engine) as s:
        s.add(JobRunORM(id=run_id, workspace="ws", job="j", status="pending"))
        s.commit()
    run_job(job, tmp_path, db_url, run_id=run_id)
    with Session(global_engine) as s:
        assert s.get(JobRunORM, run_id).status == "success"


def test_error_task_forces_run_failed(tmp_path, db_url, global_engine):
    from dataforge.db import JobRunORM
    from sqlalchemy.orm import Session
    write_script(tmp_path, "ok.py", "print('ok')\n")
    (tmp_path / "logs").mkdir()
    t1 = make_task("t1", "ok.py")
    t2 = Task(name="t2", type="error",
              depends_on=[{"task": "t1", "when": "succeeded"}],
              params={"message": "trip-wire"},
              retry=RetryConfig(attempts=1, delay_seconds=0))
    job = Job(name="j", tasks=[t1, t2], retry=RetryConfig(attempts=1, delay_seconds=0))
    run_id = "test-run-2"
    with Session(global_engine) as s:
        s.add(JobRunORM(id=run_id, workspace="ws", job="j", status="pending"))
        s.commit()
    run_job(job, tmp_path, db_url, run_id=run_id)
    with Session(global_engine) as s:
        assert s.get(JobRunORM, run_id).status == "failed"


def test_inactive_task_pruned(tmp_path, db_url):
    write_script(tmp_path, "ok.py", "print('ok')\n")
    (tmp_path / "logs").mkdir()
    task = make_task("t_off", "ok.py", active=False)
    job = Job(name="j", tasks=[task], retry=RetryConfig(attempts=1, delay_seconds=0))
    results = run_job(job, tmp_path, db_url)
    assert results.get("t_off") is None


def test_job_reference_recursion(tmp_path, db_url):
    """A task.job reference triggers load_job from disk + recursive run_job."""
    # Set up a minimal workspace layout that load_job() expects:
    # {workspace_dir}/workspace.yaml
    # {workspace_dir}/jobs/inner_job/job.yaml
    # {workspace_dir}/scripts/inner.py
    # {workspace_dir}/logs/
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "name: test\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n"
    )
    (ws / "jobs").mkdir()
    (ws / "scripts").mkdir()
    (ws / "logs").mkdir()
    (ws / "scripts" / "inner.py").write_text("print('inner')\n")

    inner_job_dir = ws / "jobs" / "inner_job"
    inner_job_dir.mkdir()
    (inner_job_dir / "job.yaml").write_text(
        "name: inner_job\ntasks:\n"
        "  - name: inner_t1\n"
        "    script: inner.py\n"
        "    retry:\n      attempts: 1\n      delay_seconds: 0\n"
    )

    outer_task = Task(
        name="outer_t1",
        job="inner_job",
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    outer_job = Job(
        name="outer_job",
        tasks=[outer_task],
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    results = run_job(outer_job, ws, db_url)
    assert results.get("outer_t1") == 0


def test_db_thread_safety(tmp_path, db_url):
    """Two parallel tasks must not share a SQLAlchemy Session."""
    write_script(tmp_path, "a.py", "print('a')\n")
    write_script(tmp_path, "b.py", "print('b')\n")
    (tmp_path / "logs").mkdir()
    t1 = make_task("t1", "a.py")
    t2 = make_task("t2", "b.py")
    job = Job(name="j", tasks=[t1, t2], retry=RetryConfig(attempts=1, delay_seconds=0))
    results = run_job(job, tmp_path, db_url)
    assert results["t1"] == 0
    assert results["t2"] == 0
