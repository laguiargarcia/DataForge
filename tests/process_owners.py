"""Owners for run rows in tests: a process that has ended, as dataforge.liveness records one."""
import dataclasses
import subprocess
import sys

from dataforge.liveness import current_owner


def ended_pid() -> int:
    """The pid of a child process that has run and been waited for."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def dead_owner_text() -> str:
    """An owner on this machine whose process has ended. Its start time ('0') matches no live
    process, so the row stays orphaned even if the OS reuses the pid meanwhile."""
    return dataclasses.replace(current_owner(), pid=ended_pid(), start="0").dumps()
