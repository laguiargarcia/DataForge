"""The real DataForge API on a loopback socket, for tests that follow it the way its clients do.

httpx's ASGITransport and Starlette's TestClient hand back a response only once the app has
finished it, so neither can follow a live SSE stream, and Qt's QNetworkAccessManager needs a real
socket. serve_api() runs `dataforge.api:app` under uvicorn in a background thread, as the desktop
app's DaemonThread does (tests/test_gui_daemon.py): start it, wait for /health, stop it in a
finally. The thread allocates while GUI tests keep widgets alive; that is safe only because
tests/conftest.py keeps the automatic garbage collector off (df-3yh): never collect from it.

DATAFORGE_HOME must be set before serve_api() is entered: the server reads it on every request,
and its startup reconciles the orphaned runs found there (df-bw7).
"""
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx
import uvicorn

_START_TIMEOUT = 10.0

# A one-task job whose script waits for a file named 'release' next to it, so the test decides when
# the task ends and sees the run in progress first. It exits with the number written in the file,
# and gives up after 30 s so a broken test cannot leave a run going forever.
GATED_JOB, GATED_TASK = "gated", "extract"
GATED_OUTPUT = "extracting\nWARN two rows skipped\n"
_GATED_SCRIPT = """\
import pathlib, sys, time
release = pathlib.Path(__file__).with_name("release")
deadline = time.monotonic() + 30
while not release.exists():
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.02)
print("extracting")
print("WARN two rows skipped")
sys.exit(int(release.read_text(encoding="utf-8") or 0))
"""


def add_gated_job(workspace_dir: Path) -> None:
    (workspace_dir / "scripts" / "gated.py").write_bytes(_GATED_SCRIPT.encode("utf-8"))
    job_dir = workspace_dir / "jobs" / GATED_JOB
    job_dir.mkdir(parents=True)
    (job_dir / "job.yaml").write_bytes(
        f"name: {GATED_JOB}\ntasks:\n  - name: {GATED_TASK}\n    script: gated.py\n"
        "    retry:\n      attempts: 1\n      delay_seconds: 0\n".encode("utf-8"))


def release_gated_job(workspace_dir: Path, exit_code: int = 0) -> None:
    (workspace_dir / "scripts" / "release").write_bytes(str(exit_code).encode("utf-8"))


@contextmanager
def serve_api() -> Iterator[str]:
    """Run the API on a free loopback port; yield its base URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))   # bound here and handed over: no race for the port
    port = sock.getsockname()[1]
    config = uvicorn.Config("dataforge.api:app", host="127.0.0.1", port=port,
                            log_level="warning", timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                              daemon=True, name="test-live-api")
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(base, thread)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def _wait_until_up(base: str, thread: threading.Thread) -> None:
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise AssertionError("the API server thread ended before it answered /health")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"the API did not answer /health within {_START_TIMEOUT}s")
