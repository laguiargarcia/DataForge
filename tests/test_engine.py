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


def _sub_job_workspace(tmp_path, inner_tasks_yaml):
    """A workspace whose job `inner` is run by a `job`-type task; ok.py exits 0, fail.py 1."""
    ws = tmp_path / "ws"
    (ws / "jobs" / "inner").mkdir(parents=True)
    (ws / "logs").mkdir()
    write_script(ws, "ok.py", "print('ok')\n")
    write_script(ws, "fail.py", "import sys; sys.exit(1)\n")
    (ws / "jobs" / "inner" / "job.yaml").write_text(
        "name: inner\ntasks:\n" + inner_tasks_yaml.replace(
            "\n    script:", "\n    retry: {attempts: 1, delay_seconds: 0}\n    script:"))
    return ws


def _run_sub_job(ws, db_url):
    outer = Task(name="outer", job="inner", retry=RetryConfig(attempts=1, delay_seconds=0))
    return run_job(Job(name="outer_job", tasks=[outer],
                       retry=RetryConfig(attempts=1, delay_seconds=0)), ws, db_url)["outer"]


def test_a_sub_job_with_a_skipped_handler_succeeds(tmp_path, db_url):
    """A skipped task is neutral in a sub-job too: its `job` task must not read it as a failure."""
    ws = _sub_job_workspace(tmp_path,
                            "  - name: t1\n    script: ok.py\n"
                            "  - name: notify\n    script: ok.py\n"
                            "    depends_on: [{task: t1, when: failed}]\n")
    assert _run_sub_job(ws, db_url) == 0


def test_a_sub_job_with_a_failure_nobody_takes_over_fails(tmp_path, db_url):
    ws = _sub_job_workspace(tmp_path,
                            "  - name: t1\n    script: fail.py\n"
                            "  - name: t2\n    script: ok.py\n    depends_on: [t1]\n")
    assert _run_sub_job(ws, db_url) == 1


def test_a_sub_job_whose_failure_is_taken_over_succeeds(tmp_path, db_url):
    ws = _sub_job_workspace(tmp_path,
                            "  - name: t1\n    script: fail.py\n"
                            "  - name: notify\n    script: ok.py\n"
                            "    depends_on: [{task: t1, when: failed}]\n")
    assert _run_sub_job(ws, db_url) == 0


def test_job_aninhado_no_mesmo_workspace_nao_trava(dataforge_home, db_url):
    """A nested `type: job` task re-enters the workspace lock (same run_id) instead of deadlocking."""
    import time

    ws = dataforge_home / "workspaces" / "nested_ws"
    (ws / "jobs" / "inner_job").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "logs").mkdir()
    (ws / "workspace.yaml").write_text("name: nested_ws\n")
    (ws / "scripts" / "inner.py").write_text("print('inner')\n")
    (ws / "jobs" / "inner_job" / "job.yaml").write_text(
        "name: inner_job\ntasks:\n  - name: t1\n    script: inner.py\n"
        "    retry:\n      attempts: 1\n      delay_seconds: 0\n"
    )
    outer = Job(
        name="outer_job",
        tasks=[Task(name="call_inner", job="inner_job", retry=RetryConfig(attempts=1, delay_seconds=0))],
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    import threading

    box = {}
    t = threading.Thread(
        target=lambda: box.update(results=run_job(outer, ws, db_url, run_id="nested-run")), daemon=True
    )
    start = time.monotonic()
    t.start()
    t.join(10)
    assert not t.is_alive(), "nested job deadlocked on the workspace lock"
    assert box["results"].get("call_inner") == 0
    assert time.monotonic() - start < 10


def test_run_job_avisa_quando_espera_a_trava(dataforge_home, db_url):
    """on_wait reaches the workspace lock: called once while another run holds it, never when free."""
    import threading
    from dataforge.runlock import workspace_run_lock

    ws = dataforge_home / "workspaces" / "wait_ws"
    write_script(ws, "ok.py", "print('ok')\n")
    (ws / "logs").mkdir()
    job = Job(name="j", tasks=[make_task("t1", "ok.py")], retry=RetryConfig(attempts=1, delay_seconds=0))
    waits, box = [], {}
    waiting = threading.Event()

    def on_wait():
        waits.append(1)
        waiting.set()

    with workspace_run_lock(ws, "outro-run"):
        t = threading.Thread(target=lambda: box.update(r=run_job(job, ws, db_url, on_wait=on_wait)), daemon=True)
        t.start()
        assert waiting.wait(5)
    t.join(10)
    assert box["r"] == {"t1": 0}
    assert waits == [1]
    run_job(job, ws, db_url, on_wait=on_wait)  # free: no call
    assert waits == [1]


def test_a_queued_run_stays_pending_then_ends_failed_under_the_lock(dataforge_home, db_url, global_engine):
    """df-1mdh x df-h864: while another run holds the workspace lock the row stays `pending`
    (on_running not called yet); once the lock is ours it reads `running` when on_running fires,
    and a failure nobody takes over (a fetch whose chain was skipped) still ends the run `failed`."""
    import threading
    from sqlalchemy.orm import Session
    from dataforge.db import JobRunORM
    from dataforge.runlock import workspace_run_lock

    ws = dataforge_home / "workspaces" / "queue_ws"
    write_script(ws, "fail.py", "import sys; sys.exit(1)\n")
    write_script(ws, "ok.py", "print('ok')\n")
    (ws / "logs").mkdir()
    job = Job(name="j", tasks=[make_task("fetch", "fail.py"), make_task("report", "ok.py", depends_on=["fetch"])],
              retry=RetryConfig(attempts=1, delay_seconds=0))
    run_id = "queued-run"
    with Session(global_engine) as s:
        s.add(JobRunORM(id=run_id, workspace="queue_ws", job="j", status="pending"))
        s.commit()

    def status():
        with Session(global_engine) as s:
            return s.get(JobRunORM, run_id).status

    seen, box = {}, {}
    waiting = threading.Event()

    def on_wait():
        seen["waiting"] = status()
        waiting.set()

    def on_running():
        seen["running"] = status()

    with workspace_run_lock(ws, "outro-run"):
        t = threading.Thread(target=lambda: box.update(r=run_job(
            job, ws, db_url, run_id, on_running=on_running, on_wait=on_wait)), daemon=True)
        t.start()
        assert waiting.wait(5)
        assert status() == "pending"
        assert "running" not in seen
    t.join(30)
    assert box["r"] == {"fetch": 1, "report": "skipped"}
    assert seen == {"waiting": "pending", "running": "running"}
    assert status() == "failed"


def test_duas_execucoes_do_mesmo_workspace_no_mesmo_processo_fazem_fila(dataforge_home, db_url):
    """Two runs of one workspace in one process queue on the run lock: the lock is keyed by each
    run's own run_id, so the second run waits (on_wait once) and its task starts after the first's
    task ended. Reentry is only for the same run_id (a nested `type: job`)."""
    import threading
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from dataforge.db import ExecucaoORM, init_db

    ws = dataforge_home / "workspaces" / "fila_ws"
    write_script(ws, "lento.py", "import time\ntime.sleep(1)\n")
    (ws / "logs").mkdir()
    job = Job(name="j", tasks=[make_task("t1", "lento.py")], retry=RetryConfig(attempts=1, delay_seconds=0))
    started = threading.Event()
    waits, box = [], {}

    a = threading.Thread(
        target=lambda: box.update(a=run_job(job, ws, db_url, "run-a", on_running=started.set)), daemon=True,
    )
    a.start()
    assert started.wait(10), "run-a never took the lock"
    box["b"] = run_job(job, ws, db_url, "run-b", on_wait=lambda: waits.append(1))
    a.join(30)
    assert not a.is_alive()
    assert box == {"a": {"t1": 0}, "b": {"t1": 0}}
    assert waits == [1]

    with Session(init_db(db_url)) as s:
        rows = {r.run_id: r for r in s.scalars(select(ExecucaoORM).where(ExecucaoORM.task == "t1"))}
    assert set(rows) == {"run-a", "run-b"}
    assert rows["run-a"].fim <= rows["run-b"].inicio, "the two runs' tasks overlapped"
