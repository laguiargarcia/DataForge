"""Runs a workspace app's ASGI backend as a separate uvicorn process, one per app.

Started on first use on a free loopback port with a per-start secret, restarted when it died or
its ``backend/`` code changed, stopped when its windows closed or it sat idle. The ``.env`` of the
workspace is never passed on: the backend only gets what the user's own environment has plus the
DATAFORGE_* variables below.
"""
import asyncio
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from pathlib import Path

from . import compat
from .apps import AppInfo
from .presence import PresenceRegistry

START_TIMEOUT_SECONDS = 20
IDLE_SECONDS = 900
CODE_CHECK_SECONDS = 3
_STOP_WAIT_SECONDS = 5
_POLL_SECONDS = 0.1
_LOG_TAIL_BYTES = 2000


class BackendUnavailable(Exception):
    def __init__(self, reason: str, log_path: Path) -> None:
        super().__init__(reason)
        self.reason = reason
        self.log_path = log_path


@dataclass
class _Running:
    process: subprocess.Popen
    port: int
    token: str
    fingerprint: str
    last_check: float
    last_call: float
    window_seen: bool = False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _workspace_dir(info: AppInfo) -> Path:
    try:
        return compat.resolve_workspace(info.workspace)
    except ValueError:
        return info.app_dir.parent.parent  # not under the data dir (should not happen): the app's own folder tree


def log_path(info: AppInfo) -> Path:
    return _workspace_dir(info) / "logs" / f"app_{info.name}.log"


def _backend_fingerprint(info: AppInfo) -> str:
    """Hash of (path, size, mtime_ns) of every file under backend/ except __pycache__."""
    root = info.app_dir / "backend"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue  # vanished between listing and stat: the next check sees the new state
        digest.update(f"{path.relative_to(root).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _log_tail(path: Path) -> str:
    try:
        return path.read_bytes()[-_LOG_TAIL_BYTES:].decode("utf-8", "replace").strip()
    except OSError:
        return ""


async def _accepts(port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 1)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(_STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
    process.wait()


_AFTER_ACCEPT_SECONDS = 0.3  # a foreign listener on the port makes our uvicorn exit right after binding


def _spawn(info: AppInfo, port: int, token: str, logfile: Path) -> subprocess.Popen:
    env = {
        **os.environ,  # noqa: TID251 -- the backend inherits the user's environment (PYTHONPATH, PATH...), not the workspace .env
        "DATAFORGE_HOME": str(compat.data_dir()),
        "DATAFORGE_WORKSPACE": info.workspace,
        "DATAFORGE_WORKSPACE_DIR": str(_workspace_dir(info)),
        "DATAFORGE_API": f"http://127.0.0.1:{compat.api_port()}",
        "DATAFORGE_APP_TOKEN": token,
    }
    with open(logfile, "wb") as log:  # the child keeps its own copy of the handle
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", info.backend, "--host", "127.0.0.1", "--port", str(port)],
            cwd=info.app_dir, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )


class BackendSupervisor:
    def __init__(self) -> None:
        self._running: dict[tuple[str, str], _Running] = {}
        # asyncio.Lock is bound to one event loop: keep one table of locks per loop.
        self._locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._state = threading.RLock()

    def _lock_for(self, key: tuple[str, str]) -> asyncio.Lock:
        per_loop = self._locks.setdefault(asyncio.get_running_loop(), {})
        if key not in per_loop:
            per_loop[key] = asyncio.Lock()
        return per_loop[key]

    async def target(self, info: AppInfo) -> tuple[int, str]:
        """Port and secret of the app's backend, starting or restarting it when needed."""
        key = (info.workspace, info.name)
        async with self._lock_for(key):
            current = self._running.get(key)
            if current is not None:
                if self._usable(info, current):
                    return current.port, current.token
                await asyncio.to_thread(self.stop, *key)
            started = await self._start(info)
            with self._state:
                self._running[key] = started
            return started.port, started.token

    def _usable(self, info: AppInfo, current: _Running) -> bool:
        if current.process.poll() is not None:
            return False
        now = time.time()
        if now - current.last_check >= CODE_CHECK_SECONDS:
            current.last_check = now
            if _backend_fingerprint(info) != current.fingerprint:
                return False
        return True

    async def _start(self, info: AppInfo) -> _Running:
        logfile = log_path(info)
        try:
            logfile.parent.mkdir(parents=True, exist_ok=True)
            port, token = _free_port(), secrets.token_urlsafe(32)
            fingerprint = _backend_fingerprint(info)
            process = _spawn(info, port, token, logfile)
        except OSError as exc:
            raise BackendUnavailable(f"não foi possível iniciar o backend: {exc}", logfile) from exc
        try:
            await self._wait_ready(process, port, logfile)
        except BaseException:  # includes cancellation: never leave an unregistered process holding the secret
            _terminate(process)
            raise
        now = time.time()
        return _Running(process, port, token, fingerprint, now, now)

    async def _wait_ready(self, process: subprocess.Popen, port: int, logfile: Path) -> None:
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while True:
            if process.poll() is not None:
                raise BackendUnavailable(f"o backend terminou ao subir: {_log_tail(logfile)}", logfile)
            if await _accepts(port):
                await asyncio.sleep(_AFTER_ACCEPT_SECONDS)
                if process.poll() is not None:
                    raise BackendUnavailable(f"o backend terminou ao subir: {_log_tail(logfile)}", logfile)
                return
            if time.monotonic() >= deadline:
                raise BackendUnavailable(f"o backend não respondeu em {START_TIMEOUT_SECONDS} s", logfile)
            await asyncio.sleep(_POLL_SECONDS)

    def record_call(self, workspace: str, app: str, now: float) -> None:
        with self._state:
            current = self._running.get((workspace, app))
            if current is not None:
                current.last_call = now

    def sweep(self, now: float, presence: PresenceRegistry) -> list[tuple[str, str]]:
        """Stop backends whose windows all closed (after one was seen) or that sat idle."""
        due: list[tuple[tuple[str, str], _Running]] = []
        with self._state:
            for key, current in list(self._running.items()):
                if presence.app_windows(key[0], key[1], now) > 0:
                    current.window_seen = True
                    closed = False
                else:
                    closed = current.window_seen
                if closed or now - current.last_call > IDLE_SECONDS:
                    del self._running[key]
                    due.append((key, current))
        for _, current in due:  # terminating can take seconds: not under the lock
            _terminate(current.process)
        return [key for key, _ in due]

    def stop(self, workspace: str, app: str) -> None:
        with self._state:
            current = self._running.pop((workspace, app), None)
        if current is not None:
            _terminate(current.process)

    def stop_all(self) -> None:
        with self._state:
            keys = list(self._running)
        for workspace, app in keys:
            self.stop(workspace, app)


supervisor = BackendSupervisor()
