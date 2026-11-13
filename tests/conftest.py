import gc
import os

# Before anything imports Qt: GUI tests run headless unless the caller chose a platform. Under WSLg a
# real window per test turns ~5 s of GUI tests into ~5 min (df-m4tz); CI sets the same value.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pathlib import Path
from hypothesis import settings
from fastapi.testclient import TestClient
from dataforge.db import init_db
from dataforge.compat import db_url as make_db_url

settings.register_profile("deterministic", derandomize=True, database=None, deadline=None)
# Ordinary runs: the profile Hypothesis picked on its own ("default" locally: random, with the example
# database; "ci" when it detects CI, which already has no deadline), without the 200 ms per-example
# deadline, so a property on a slow disk fails on its rule, never on time. Hypothesis's pytest plugin
# loads a profile named on the command line (--hypothesis-profile, as `testloop check` passes) after
# this file is imported, so that one still wins.
settings.register_profile("no-deadline", settings.default, deadline=None)
settings.load_profile("no-deadline")


def pytest_configure(config):
    # Registered here so a clone without the local pytest.ini (as in CI) knows the marker.
    config.addinivalue_line(
        "markers",
        "integration: slow end-to-end tests (deselect with -m 'not integration')",
    )


@pytest.fixture
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


@pytest.fixture(scope="session", autouse=True)
def gc_on_main_thread_only():
    """Run the cyclic garbage collector on the main thread only, between tests (df-3yh).

    The automatic collector runs in whichever thread crosses the allocation threshold: the
    daemon thread of test_gui_daemon or the portal thread of a TestClient. A collection there
    that frees a widget left over by a GUI test destroys it off the GUI thread, and the process
    segfaults. The desktop app applies the same rule (dataforge/gui/gc_guard.py); its helper is
    not imported here because importing dataforge.gui loads Qt into every test run.
    """
    gc.disable()
    yield
    gc.collect()    # every leftover widget dies here, on the main thread
    gc.enable()


@pytest.fixture(autouse=True)
def collect_garbage_after_test(gc_on_main_thread_only):
    """The main-thread half of gc_on_main_thread_only: CPython's own policy, run at teardown."""
    yield
    counts, thresholds = gc.get_count(), gc.get_threshold()
    for generation in (2, 1, 0):
        if 0 < thresholds[generation] < counts[generation]:
            gc.collect(generation)
            break


@pytest.fixture(autouse=True)
def allowed_test_hosts(monkeypatch):
    """Let the in-process test clients (Host: testserver / test) through the Host check."""
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "testserver,test")


@pytest.fixture(autouse=True)
def restore_api_port():
    """``dataforge serve`` writes DATAFORGE_PORT into os.environ: put the value back after each test."""
    before = os.environ.get("DATAFORGE_PORT")
    yield
    if before is None:
        os.environ.pop("DATAFORGE_PORT", None)
    else:
        os.environ["DATAFORGE_PORT"] = before


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


@pytest.fixture
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


_BACKEND_MAIN = '''\
import os
from fastapi import FastAPI, Request
from dataforge.app_guard import AppTokenMiddleware

app = FastAPI()
app.add_middleware(AppTokenMiddleware)


@app.api_route("/eco", methods=["GET", "POST"])
async def eco(request: Request):
    return {"method": request.method, "query": request.url.query,
            "body": (await request.body()).decode(), "headers": dict(request.headers)}


@app.get("/pid")
def pid():
    return {"pid": os.getpid()}


@app.get("/env")
def env():
    names = ["DATAFORGE_HOME", "DATAFORGE_WORKSPACE", "DATAFORGE_WORKSPACE_DIR",
             "DATAFORGE_API", "DATAFORGE_APP_TOKEN"]
    out = {n: os.environ.get(n) for n in names}
    out["DATAFORGE_APP_TOKEN"] = "***" if out["DATAFORGE_APP_TOKEN"] else None
    out["SEGREDO_DO_BANCO"] = os.environ.get("SEGREDO_DO_BANCO")
    return out
'''


@pytest.fixture
def make_backend_app(make_app, monkeypatch):
    """Factory: an app with ``backend: backend.main:app`` (guarded FastAPI echo app); returns AppInfo.

    The backend runs in a child uvicorn that imports ``dataforge.app_guard``: PYTHONPATH points it
    at this checkout's package, whatever the cwd."""
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent))

    def _make(ws: Path, pasta: str = "painel"):
        from dataforge.apps import load_app
        app_dir = make_app(ws, pasta, raw_yaml=f"name: {pasta}\ntitle: App\nbackend: backend.main:app\n")
        (app_dir / "backend").mkdir(exist_ok=True)
        (app_dir / "backend" / "main.py").write_text(_BACKEND_MAIN, encoding="utf-8")
        return load_app(ws, pasta)
    return _make
