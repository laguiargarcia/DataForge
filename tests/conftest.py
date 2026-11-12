import os
import pytest
from pathlib import Path
from hypothesis import settings
from fastapi.testclient import TestClient
from dataforge.db import init_db
from dataforge.compat import db_url as make_db_url

settings.register_profile("deterministic", derandomize=True, database=None, deadline=None)


def pytest_configure(config):
    # Registered here so a clone without the local pytest.ini (as in CI) knows the marker.
    config.addinivalue_line(
        "markers",
        "integration: slow end-to-end tests (deselect with -m 'not integration')",
    )


@pytest.fixture()
def db_url(tmp_path):
    """Isolated SQLite DB per test. Never use a real path."""
    return make_db_url(tmp_path / "dataforge.db")


@pytest.fixture
def dataforge_home(tmp_path, monkeypatch):
    """Sets DATAFORGE_HOME to a temp dir and creates workspaces/ subdirectory."""
    home = tmp_path / "dataforge_home"
    home.mkdir()
    (home / "workspaces").mkdir()
    monkeypatch.setenv("DATAFORGE_HOME", str(home))
    return home


@pytest.fixture
def workspace_dir(dataforge_home):
    """Creates test workspace under DATAFORGE_HOME/workspaces/test_ws."""
    ws = dataforge_home / "workspaces" / "test_ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test_ws\n")
    (ws / "jobs").mkdir()
    (ws / "scripts").mkdir()
    return ws


@pytest.fixture
def global_engine(dataforge_home):
    """SQLAlchemy engine for the global DB at DATAFORGE_HOME/dataforge.db."""
    url = make_db_url(dataforge_home / "dataforge.db")
    return init_db(url)


@pytest.fixture
def workspace_engine(workspace_dir):
    """SQLAlchemy engine for the per-workspace DB at workspace_dir/dataforge.db."""
    url = make_db_url(workspace_dir / "dataforge.db")
    return init_db(url)


@pytest.fixture
def test_client(dataforge_home):
    """FastAPI TestClient with DATAFORGE_HOME set before app is used."""
    from dataforge.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def allowed_test_hosts(monkeypatch):
    """Let the in-process test clients (Host: testserver / test) through the Host check."""
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "testserver,test")


@pytest.fixture(autouse=True)
def no_os_scheduler(monkeypatch):
    """Block all tests from touching the real OS scheduler (crontab / Task Scheduler)."""
    from unittest.mock import MagicMock
    monkeypatch.setattr(
        "dataforge.api.routers.triggers._platform_register_trigger",
        MagicMock(),
    )
    monkeypatch.setattr(
        "dataforge.api.routers.triggers._platform_deregister_trigger",
        MagicMock(),
    )


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Fail any test whose retry would really sleep (delay_seconds > 0, docs/testing.md).

    A missing retry block means the default 3 x 30s: two API tests once slept 60s each (df-t242).
    The sleep is skipped and the test fails at teardown, since a raise inside the job's background
    task would be swallowed by its error handling.
    """
    import types
    import dataforge.engine as engine

    slept = []
    monkeypatch.setattr(engine, "time", types.SimpleNamespace(
        sleep=lambda seconds: slept.append(seconds) if seconds > 0 else None,
    ))
    yield
    if slept:
        pytest.fail(
            f"retry slept {slept}s: give the test's job/task retry delay_seconds: 0", pytrace=False
        )


@pytest.fixture()
def simple_job_dir(workspace_dir):
    """Job dir with job.yaml + real script. delay_seconds=0 is mandatory (engine calls real time.sleep)."""
    job_dir = workspace_dir / "jobs" / "test_job"
    job_dir.mkdir(parents=True, exist_ok=True)
    script = workspace_dir / "scripts" / "hello.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('hello')\n")
    (job_dir / "job.yaml").write_text(
        "name: test_job\ntasks:\n"
        "  - name: t1\n    script: hello.py\n"
        "    retry:\n      attempts: 1\n      delay_seconds: 0\n"
    )
    return job_dir


@pytest.fixture
def make_app():
    """Factory that creates apps/<pasta>/app.yaml (+ dist/index.html) under a workspace."""
    def _make(ws: Path, pasta: str, *, name: str | None = None, title: str | None = "App",
              dist: bool = True, raw_yaml: str | None = None) -> Path:
        app_dir = ws / "apps" / pasta
        app_dir.mkdir(parents=True, exist_ok=True)
        if raw_yaml is None:
            lines = [f"name: {name if name is not None else pasta}"]
            if title is not None:
                lines.append(f"title: {title}")
            raw_yaml = "\n".join(lines) + "\n"
        (app_dir / "app.yaml").write_text(raw_yaml, encoding="utf-8")
        if dist:
            (app_dir / "dist").mkdir(exist_ok=True)
            (app_dir / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
        return app_dir
    return _make
