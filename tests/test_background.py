"""Auto-shutdown rule and the real background process (integration)."""
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from dataforge import instance

T0 = 1_000_000.0


@pytest.mark.parametrize(
    ("empty_since", "jobs", "now", "expected"),
    [
        (None, 0, T0, False),
        (T0, 1, T0 + 120, False),
        (T0, 0, T0 + 59, False),
        (T0, 0, T0 + 60, True),
    ],
)
def test_should_auto_shutdown(empty_since, jobs, now, expected):
    assert instance.should_auto_shutdown(empty_since, jobs, now) is expected


def test_grace_seconds_from_env(monkeypatch):
    monkeypatch.delenv("DATAFORGE_GRACE_SECONDS", raising=False)
    assert instance.grace_seconds() == 60
    monkeypatch.setenv("DATAFORGE_GRACE_SECONDS", "2")
    assert instance.grace_seconds() == 2
    assert instance.should_auto_shutdown(T0, 0, T0 + 2) is True
    monkeypatch.setenv("DATAFORGE_GRACE_SECONDS", "lixo")
    assert instance.grace_seconds() == 60


def test_python_m_dataforge_runs():
    root = Path(__file__).resolve().parent.parent
    # The help text is pt-BR (non-ASCII): the child writes UTF-8 and the parent reads UTF-8, whatever
    # the locale (CI's Windows job sets PYTHONIOENCODING=utf-8 while text=True alone reads cp1252).
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-m", "dataforge", "--help"], capture_output=True, encoding="utf-8",
                         env=env, timeout=60, check=False)
    assert out.returncode == 0
    assert "stop" in out.stdout


# ---------------------------------------------------------------- integration

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bg_env(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parent.parent
    port = _free_port()
    (tmp_path / "workspaces").mkdir()
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setenv("DATAFORGE_PORT", str(port))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("PYTHONPATH", str(root))
    monkeypatch.setenv("DATAFORGE_GRACE_SECONDS", "3")
    yield port
    # Cleanup: ask politely, then kill whatever still owns the port.
    with contextlib.suppress(httpx.HTTPError):
        httpx.post(f"http://127.0.0.1:{port}/api/instance/shutdown", params={"force": "true"}, timeout=2, trust_env=False)
    for proc in instance._started:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    instance._started.clear()


def _wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def _kind(port):
    return instance.probe(port)[0]


@pytest.mark.integration
def test_background_ponta_a_ponta(bg_env):
    port = bg_env
    instance.start_background()
    assert _wait_for(lambda: _kind(port) is instance.Probe.OURS, 30)
    # a window keeps it alive past the grace period
    base = f"http://127.0.0.1:{port}/api/instance"
    httpx.post(f"{base}/presence", json={"client_id": "w1", "kind": "admin"}, trust_env=False).raise_for_status()
    time.sleep(5)
    assert _kind(port) is instance.Probe.OURS
    from dataforge.cli import app
    from typer.testing import CliRunner
    result = CliRunner().invoke(app, ["stop"])
    assert result.exit_code == 0, result.output
    assert _wait_for(lambda: _kind(port) is instance.Probe.NONE, 10)


@pytest.mark.integration
def test_background_desliga_sozinho(bg_env):
    port = bg_env
    instance.start_background()
    assert _wait_for(lambda: _kind(port) is instance.Probe.OURS, 30)
    base = f"http://127.0.0.1:{port}/api/instance"
    httpx.post(f"{base}/presence", json={"client_id": "w1", "kind": "admin"}, trust_env=False).raise_for_status()
    httpx.delete(f"{base}/presence/w1", trust_env=False).raise_for_status()
    proc = instance._started[-1]
    assert _wait_for(lambda: proc.poll() is not None, 40)
    assert _kind(port) is instance.Probe.NONE
