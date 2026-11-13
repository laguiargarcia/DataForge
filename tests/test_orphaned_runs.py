"""Orphaned runs (df-bw7, df-ahua.5.6) with a real `dataforge run` child process.

A run whose process is still alive must be left alone by every reconciliation (it may be a
scheduled run or one started in a terminal while the daemon starts), and published as running.
A run whose process was killed must end `failed`, with the reason, and is not published as
running. Nothing is ever re-run (df-413)."""
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dataforge.compat import db_url
from dataforge.db import ExecucaoORM, JobRunORM, init_db
from dataforge.liveness import parse_owner

_ROOT = Path(__file__).resolve().parent.parent
_JOB = "name: j\ntasks:\n  - {name: t, script: hold.py}\n"
# The task holds the run open until the test writes scripts/release (at most 5 minutes).
_HOLD = """import pathlib, time
gate = pathlib.Path(__file__).with_name("release")
deadline = time.monotonic() + 300
while not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
print("released")
"""
_WAIT = 120   # seconds the child may take to reach its task (importing dataforge is slow on /mnt/c)


def _read(path: Path, sql: str) -> list[tuple]:
    """Rows of a read-only query, or [] while the child has not created the database or table."""
    if not path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []


class _Run:
    """`dataforge run w j` in a child process, held open inside its only task."""

    def __init__(self, home: Path) -> None:
        self.home = home
        ws = home / "workspaces" / "w"
        (ws / "jobs" / "j").mkdir(parents=True)
        (ws / "scripts").mkdir()
        (ws / "workspace.yaml").write_text("name: w\n", encoding="utf-8")
        (ws / "jobs" / "j" / "job.yaml").write_text(_JOB, encoding="utf-8")
        (ws / "scripts" / "hold.py").write_text(_HOLD, encoding="utf-8")
        self.release_file = ws / "scripts" / "release"
        self.task_db = ws / "dataforge.db"
        env = {**os.environ, "DATAFORGE_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dataforge.cli", "run", "w", "j"], cwd=_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def wait_until_task_runs(self) -> None:
        deadline = time.monotonic() + _WAIT
        while time.monotonic() < deadline:
            if _read(self.task_db, "SELECT status FROM execucoes") == [("running",)]:
                return
            assert self.proc.poll() is None, f"the run ended early:\n{self.proc.communicate()[0]}"
            time.sleep(0.1)
        pytest.fail("the child never started its task")

    def release(self) -> None:
        self.release_file.write_text("", encoding="utf-8")

    def stop(self) -> None:
        self.release()   # also ends the task's script if its parent was killed
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.communicate(timeout=_WAIT)


@pytest.fixture
def held_run(dataforge_home):
    run = _Run(dataforge_home)
    try:
        run.wait_until_task_runs()
        yield run
    finally:
        run.stop()


def _start_daemon() -> list[dict]:
    """Run the API lifespan once; return the events it published."""
    published: list[dict] = []

    async def _publish(event):
        published.append(event)

    broadcaster = MagicMock()
    broadcaster.publish = _publish
    with patch("dataforge.api.broadcaster.get_broadcaster", return_value=broadcaster):
        from dataforge.api import app
        with TestClient(app):
            pass
    return published


def _run_row(home: Path) -> JobRunORM:
    with Session(init_db(db_url(home / "dataforge.db"))) as s:
        (run,) = s.query(JobRunORM).all()
        s.expunge(run)
        return run


def _task_row(home: Path) -> ExecucaoORM:
    with Session(init_db(db_url(home / "workspaces" / "w" / "dataforge.db"))) as s:
        (task,) = s.query(ExecucaoORM).all()
        s.expunge(task)
        return task


def test_a_run_whose_process_is_alive_is_left_alone_and_published_as_running(held_run):
    """The daemon starts (or restarts) while a `dataforge run` in another process is mid-task:
    its rows stay in progress, the startup scan publishes it as running, the history shows it
    running, and the run then finishes with its own outcome (df-bw7: never fail a live run)."""
    home = held_run.home
    published = _start_daemon()
    from dataforge.api import app
    with TestClient(app) as client:
        (item,) = client.get("/api/workspaces/w/executions").json()["items"]

    run = _run_row(home)
    assert (run.status, run.finished_at) == ("running", None)
    assert (_task_row(home).status, _task_row(home).fim) == ("running", None)
    assert {"workspace": "w", "job": "j", "run_id": run.id, "status": "running"} in published
    assert (item["status"], item["finished_at"], item.get("reason")) == ("running", None, None)

    held_run.release()
    assert held_run.proc.wait(timeout=_WAIT) == 0
    assert (_run_row(home).status, _task_row(home).status) == ("success", "success")


def test_a_run_whose_process_was_killed_ends_failed_with_the_reason(held_run):
    """`dataforge run` is killed mid-task: on the next daemon start the run and its task end
    `failed`, say why, end at the last moment the run was seen alive (the task's start), and the
    run is not published as running."""
    home = held_run.home
    # Kill the process the rows name as owner: on Windows a venv's python.exe is a launcher whose
    # child runs the job, so killing the launcher alone would leave the owner alive.
    owner = parse_owner(_run_row(home).owner_process)
    os.kill(owner.pid, getattr(signal, "SIGKILL", signal.SIGTERM))   # TerminateProcess on Windows
    held_run.proc.wait(timeout=_WAIT)

    published = _start_daemon()

    run, task = _run_row(home), _task_row(home)
    assert run.status == "failed"
    assert "interrupted" in run.reason
    assert task.status == "failed"
    assert task.fim == task.inicio
    assert "interrupted" in task.output
    assert run.finished_at == task.inicio
    assert not [e for e in published if e.get("run_id") == run.id]


def _runs(home: Path) -> dict[str, tuple]:
    """{run id: (status, finished_at, reason)} of every run row."""
    with Session(init_db(db_url(home / "dataforge.db"))) as s:
        return {r.id: (r.status, r.finished_at, r.reason) for r in s.query(JobRunORM).all()}


def _second_run(home: Path) -> subprocess.Popen:
    """Another `dataforge run w j` of the same workspace: it queues on the run lock (df-1mdh)."""
    # Its "aguardando…" line is not ASCII: write and read it as UTF-8 whatever the locale.
    env = {**os.environ, "DATAFORGE_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.Popen(
        [sys.executable, "-m", "dataforge.cli", "run", "w", "j"], cwd=_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8")


def _wait_for_queued_row(home: Path, first_id: str, proc: subprocess.Popen) -> str:
    """The id of the second run's row once it exists (it stays `pending` while it waits)."""
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline:
        others = [i for i in _read(home / "dataforge.db", "SELECT id FROM job_runs") if i[0] != first_id]
        if others:
            return others[0][0]
        assert proc.poll() is None, f"the second run ended early:\n{proc.communicate()[0]}"
        time.sleep(0.1)
    pytest.fail("the second run never wrote its row")


def test_a_run_waiting_for_the_workspace_lock_is_left_alone(held_run):
    """df-1mdh x df-bw7: a run queued behind another job of its workspace sits `pending` while its
    process lives. Liveness judges the process, not the age of the row, so neither the daemon start
    nor the history read may fail it; once the lock is free it runs and ends with its own outcome."""
    home = held_run.home
    first_id = _run_row(home).id
    second = _second_run(home)
    try:
        second_id = _wait_for_queued_row(home, first_id, second)
        time.sleep(1.0)   # well past the lock's first try: the child is in its wait loop
        published = _start_daemon()
        from dataforge.api import app
        with TestClient(app) as client:
            items = client.get("/api/workspaces/w/executions").json()["items"]

        runs = _runs(home)
        assert runs[second_id] == ("pending", None, None)
        assert runs[first_id][0] == "running"
        assert {i["run_id"]: i["status"] for i in items}[second_id] == "pending"
        assert not [e for e in published if e.get("run_id") == second_id]   # not shown as running

        held_run.release()
        assert held_run.proc.wait(timeout=_WAIT) == 0
        out = second.communicate(timeout=_WAIT)[0]
        assert second.returncode == 0, out
        assert "aguardando outro job do workspace terminar…" in out
        assert {i: s for i, (s, _, _) in _runs(home).items()} == {first_id: "success", second_id: "success"}
    finally:
        if second.poll() is None:
            second.kill()
            second.communicate(timeout=_WAIT)


def test_a_run_killed_while_waiting_for_the_workspace_lock_ends_failed(held_run):
    """The converse: the queued run's process dies while it waits. Its `pending` row can never
    finish, so reconciliation fails it with the reason, and the run holding the lock is untouched."""
    home = held_run.home
    first_id = _run_row(home).id
    second = _second_run(home)
    try:
        second_id = _wait_for_queued_row(home, first_id, second)
        with Session(init_db(db_url(home / "dataforge.db"))) as s:
            owner = parse_owner(s.get(JobRunORM, second_id).owner_process)
        os.kill(owner.pid, getattr(signal, "SIGKILL", signal.SIGTERM))   # TerminateProcess on Windows
        second.wait(timeout=_WAIT)

        _start_daemon()

        runs = _runs(home)
        assert runs[second_id][0] == "failed"
        assert "interrupted" in runs[second_id][2]
        assert runs[first_id][:2] == ("running", None)
    finally:
        if second.poll() is None:
            second.kill()
        second.communicate(timeout=_WAIT)
