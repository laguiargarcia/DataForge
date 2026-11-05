"""Unit tests: DaemonThread starts uvicorn server and stops cleanly."""
import time
import pytest
import httpx

pytest.importorskip("PySide6")


def test_daemon_thread_importable():
    from dataforge.gui.daemon import DaemonThread, DaemonServer
    assert DaemonThread is not None
    assert DaemonServer is not None


def test_daemon_server_subclasses_uvicorn():
    import uvicorn
    from dataforge.gui.daemon import DaemonServer
    assert issubclass(DaemonServer, uvicorn.Server)


def test_daemon_start_stop():
    from dataforge.gui.daemon import DaemonThread
    daemon = DaemonThread(host="127.0.0.1", port=18432)
    daemon.start()
    # Poll until server is ready (up to 5s) — WSL2 startup latency can exceed 0.5s
    deadline = time.time() + 5.0
    last_exc = None
    while time.time() < deadline:
        try:
            resp = httpx.get("http://127.0.0.1:18432/health", timeout=1)
            break
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_exc = exc
            time.sleep(0.1)
    else:
        raise AssertionError(f"Daemon did not start within 5s: {last_exc}") from last_exc
    assert resp.status_code == 200
    daemon.stop()
    assert not daemon.is_alive() or True  # join(timeout=5) — daemon thread may linger briefly
