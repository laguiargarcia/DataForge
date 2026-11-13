"""Which DataForge owns the API port: identify it, find it, or start the background one.

``/health`` carries a fingerprint of the code and the home directory, so a client can tell our
DataForge from another program, another home, or an older version still running.
"""
import contextlib
import functools
import hashlib
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path

import httpx

from . import compat
from .runlock import file_lock

SERVICE = "dataforge"
_POLL_SECONDS = 0.25


def code_fingerprint(root: Path | None = None) -> str:
    """sha256 over (relative path, size, mtime_ns) of every ``**/*.py`` under ``root``.

    With ``root=None`` it covers the dataforge package and is computed once per process.
    """
    return _package_fingerprint() if root is None else _fingerprint(root)


def _fingerprint(base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*.py")):
        stat = path.stat()
        digest.update(f"{path.relative_to(base).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


@functools.cache
def _package_fingerprint() -> str:
    return _fingerprint(Path(__file__).parent)


def health_payload() -> dict:
    return {
        "status": "ok",
        "service": SERVICE,
        "version": code_fingerprint(),
        "home": str(compat.data_dir().resolve()),
    }


class Probe(Enum):
    NONE = "none"
    OURS = "ours"
    OTHER_PROGRAM = "other_program"
    OTHER_HOME = "other_home"
    OTHER_VERSION = "other_version"


class InstanceError(Exception):
    """A user-facing reason why there is no usable DataForge on the port."""


def probe(port: int, timeout: float = 1.0, client: httpx.Client | None = None) -> tuple[Probe, dict | None]:
    """Ask the port who it is. Returns the verdict and the ``/health`` body when there is one."""
    owns_client = client is None
    # trust_env=False: the loopback check must not go through HTTP_PROXY.
    client = client or httpx.Client(timeout=timeout, trust_env=False)
    try:
        try:
            response = client.get(f"http://127.0.0.1:{port}/health")
        except httpx.HTTPError:
            # Refused, unreachable or silent (timeout) all count as NONE: if a silent process does
            # own the port, the duplicate background fails to bind and says so in background.log.
            return Probe.NONE, None
    finally:
        if owns_client:
            client.close()
    try:
        body = response.json() if response.is_success else None
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get("service") != SERVICE:
        return Probe.OTHER_PROGRAM, None
    if os.path.normcase(str(body.get("home"))) != os.path.normcase(str(compat.data_dir().resolve())):
        return Probe.OTHER_HOME, body
    if body.get("version") != code_fingerprint():
        return Probe.OTHER_VERSION, body
    return Probe.OURS, body


# Windows process-creation flags (literals: subprocess only defines them on Windows).
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Handles of the processes we started: dropping a running Popen makes its GC emit a ResourceWarning.
_started: list[subprocess.Popen] = []


def spawn_detached(cmd: list[str], log_path: Path) -> None:
    """Run ``cmd`` detached (own session / process group, no console), its output appended to ``log_path``.

    On Windows it also breaks away from the caller's Job object (terminals and IDEs kill the
    children of a closing window), retrying without that flag when the Job forbids breakaway.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        common = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log, "close_fds": True}
        if sys.platform != "win32":
            _started.append(subprocess.Popen(cmd, start_new_session=True, **common))
            return
        flags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(cmd, creationflags=flags | _CREATE_BREAKAWAY_FROM_JOB, **common)
        except OSError:
            proc = subprocess.Popen(cmd, creationflags=flags, **common)
        _started.append(proc)


def start_background() -> None:
    """Launch ``python -m dataforge background`` detached and without a console."""
    spawn_detached([sys.executable, "-m", "dataforge", "background"], compat.logs_dir() / "background.log")


def refuse(kind: Probe, body: dict | None, port: int) -> None:
    if kind is Probe.OTHER_PROGRAM:
        raise InstanceError(f"a porta {port} está ocupada por outro programa; defina DATAFORGE_PORT")
    if kind is Probe.OTHER_HOME:
        raise InstanceError(f"a porta {port} é de outro DataForge ({(body or {}).get('home')})")
    if kind is Probe.OTHER_VERSION:
        raise InstanceError("há um DataForge de outra versão rodando; saia pela bandeja e abra de novo")


def ensure_server(timeout: float = 20.0) -> int:
    """Return the port of a running DataForge of ours, starting the background one if needed."""
    try:
        port = compat.api_port()
    except ValueError as exc:
        raise InstanceError(str(exc)) from exc
    kind, body = probe(port)
    if kind is Probe.OURS:
        return port
    refuse(kind, body, port)

    with file_lock(compat.locks_dir() / "instance-start.lock", poll_seconds=_POLL_SECONDS):
        kind, body = probe(port)
        if kind is Probe.OURS:
            return port
        refuse(kind, body, port)
        start_background()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            kind, body = probe(port)
            if kind is Probe.OURS:
                return port
            refuse(kind, body, port)
            time.sleep(_POLL_SECONDS)
    log = compat.logs_dir() / "background.log"
    raise InstanceError(f"o DataForge não respondeu em {timeout:g}s; veja {log}")


GRACE_SECONDS = 60


def grace_seconds() -> float:
    """How long the server stays up with no window and no job: ``$DATAFORGE_GRACE_SECONDS``, default 60."""
    return compat.grace_seconds(GRACE_SECONDS)


def should_auto_shutdown(empty_since: float | None, jobs: int, now: float) -> bool:
    return empty_since is not None and jobs == 0 and now - empty_since >= grace_seconds()


class _State:
    """Process-wide counters, mutated from worker threads and read from request threads."""

    lock = threading.Lock()
    server_jobs = 0
    shutdown_handler: Callable[[], None] | None = None


def begin_server_job() -> None:
    """Count a job (queued ones included) from now on; pair it with exactly one ``end_server_job``.

    The route calls this before queueing the job, so there is no gap between "accepted" and "counted".
    """
    with _State.lock:
        _State.server_jobs += 1


def end_server_job() -> None:
    with _State.lock:
        _State.server_jobs -= 1


@contextlib.contextmanager
def track_server_job():
    """Count a job running inside this server while the block runs."""
    begin_server_job()
    try:
        yield
    finally:
        end_server_job()


def server_jobs() -> int:
    with _State.lock:
        return _State.server_jobs


def set_shutdown_handler(fn: Callable[[], None] | None) -> None:
    """Register what stops this server; the hosting process sets it, ``None`` clears it.

    ``request_shutdown`` calls the handler on a request thread (a FastAPI threadpool thread) and
    the 202 goes out only after it returns. So the handler must only SIGNAL (thread-safe) and
    return at once: it must never block, join threads or exit the process.
    """
    _State.shutdown_handler = fn


def request_shutdown() -> bool:
    """Ask the hosting process to stop. False when it registered no handler."""
    handler = _State.shutdown_handler
    if handler is None:
        return False
    handler()
    return True
