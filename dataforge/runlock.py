"""Per-workspace run lock: jobs of one workspace run one at a time, across processes.

An exclusive OS file lock (``msvcrt.locking`` on Windows, ``fcntl.flock`` on POSIX) held for the
whole run. The OS releases it if the process dies. Reentrant for the same ``run_id`` in one
process: a ``type: job`` task runs the nested job under the parent's run_id, on another thread.

The queue only spans processes of the same system (Windows with Windows, WSL with WSL).
"""
import errno
import os
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .compat import run_lock_path

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EDEADLOCK):
                return False  # lock busy: someone else holds it
            raise
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False  # lock busy: someone else holds it
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path, poll_seconds: float = 0.5, on_wait: Callable[[], None] | None = None):
    """Block until the exclusive OS lock on ``path`` is ours; release on exit. Not reentrant.

    ``on_wait`` is called once, when the first try finds the lock taken (before waiting)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _try_lock(fd):
            if on_wait is not None:
                on_wait()
            while not _try_lock(fd):
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# {lock_path: [run_id, depth]} for locks this process holds.
_held: dict[str, list] = {}
_held_guard = threading.Lock()


def _reenter(key: str, run_id: str) -> bool:
    with _held_guard:
        entry = _held.get(key)
        if entry is not None and entry[0] == run_id:
            entry[1] += 1
            return True
    return False


@contextmanager
def workspace_run_lock(
    workspace_dir: Path, run_id: str, poll_seconds: float = 0.5, on_wait: Callable[[], None] | None = None,
):
    """Block until this workspace's run lock is ours; release when the outermost block exits.

    ``on_wait`` is called once if another run holds the lock (never on reentry)."""
    path = run_lock_path(workspace_dir)
    key = str(path)
    if _reenter(key, run_id):
        try:
            yield
        finally:
            with _held_guard:
                _held[key][1] -= 1
        return

    with file_lock(path, poll_seconds, on_wait):
        with _held_guard:
            _held[key] = [run_id, 1]
        try:
            yield
        finally:
            with _held_guard:
                entry = _held[key]
                entry[1] -= 1
                released = entry[1] == 0
                if released:
                    del _held[key]
