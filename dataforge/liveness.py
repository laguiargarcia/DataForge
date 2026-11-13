"""Which process owns a run row, and whether that process has ended (df-bw7).

The process that runs a job writes its run row (`job_runs`) and task rows (`execucoes`). If that
process dies (kill, GUI closed, power loss) no finalizer runs and the rows keep saying `pending` or
`running`. So every row records its owner (`Owner`, as JSON in `owner_process`), and
`owner_is_gone` decides whether the owner has provably ended. It answers True only on proof; any
doubt means "maybe alive", because failing the record of a live run is worse than a stale row.

- **What is recorded:** OS (`sys.platform`), host name, the Linux pid namespace and boot id, the pid,
  and when the process started (Linux: `starttime` in /proc/<pid>/stat; Windows: the creation time).
- **Another OS or host:** not judged. WSL (`linux`) and native Windows (`win32`) can open the same
  database file, but a pid of one means nothing in the other; each side reconciles its own rows.
- **Linux boot id differs:** that kernel restarted since, so every process it ran has ended.
- **Another pid namespace** (another WSL distro, a container): not judged.
- **No such process now:** ended. An exited process not yet reaped (Linux zombie) counts as ended.
- **pid reuse:** a live process with that pid but another start time is someone else: ended.
- **The caller's own process:** alive, like any live process (the daemon's in-process runs).
- **Rows without an owner** (written before owners were recorded) and unsupported platforms
  (anything but Linux and Windows): never judged.
"""
import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable

_PROC = Path("/proc")


@dataclass(frozen=True)
class Owner:
    """The process that wrote a row. Empty strings mean "not known on this platform"."""
    system: str     # sys.platform
    host: str       # socket.gethostname()
    space: str      # Linux pid namespace (readlink /proc/self/ns/pid)
    boot: str       # Linux boot id (/proc/sys/kernel/random/boot_id)
    pid: int
    start: str      # the process start time, as this platform reports it

    def dumps(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


_FIELDS = {f.name: f.type for f in fields(Owner)}


def parse_owner(text: str | None) -> Owner | None:
    """The Owner a row recorded, or None when it recorded none or something unreadable."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != set(_FIELDS):
        return None
    if not all(isinstance(data[k], int if t is int else str) for k, t in _FIELDS.items()):
        return None
    owner = Owner(**data)
    return owner if owner.pid > 0 else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return ""


def _linux_start(pid: int) -> str | None:
    try:
        stat = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError:
        return ""
    # The command name (field 2) is in parentheses and may hold spaces or ')': cut after the last.
    after_name = stat[stat.rindex(")") + 2:].split()
    if after_name[0] in ("Z", "X"):   # exited; only the parent's wait() is missing
        return None
    return after_name[19]             # field 22, starttime


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _QUERY_LIMITED_INFORMATION = 0x1000
    _ERROR_INVALID_PARAMETER = 87     # OpenProcess on a pid no process has
    _STILL_ACTIVE = 259

    def _windows_start(pid: int) -> str | None:
        handle = _kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else ""
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if not (_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                    and _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))):
                return ""
            if code.value != _STILL_ACTIVE:   # exited; someone still holds a handle to it
                return None
            return str(times[0].dwHighDateTime << 32 | times[0].dwLowDateTime)
        finally:
            _kernel32.CloseHandle(handle)


def process_start(pid: int) -> str | None:
    """When the live process `pid` of this machine started; None when no such process runs;
    "" when one runs but its start cannot be read (no permission, unsupported platform)."""
    if sys.platform == "win32":
        return _windows_start(pid)
    if sys.platform.startswith("linux"):
        return _linux_start(pid)
    return ""


def _pid_space() -> str:
    try:
        return os.readlink(_PROC / "self" / "ns" / "pid")
    except OSError:
        return ""


_current: dict[int, Owner] = {}


def current_owner() -> Owner:
    """This process, as a row records it (computed once per pid)."""
    pid = os.getpid()
    if pid not in _current:
        linux = sys.platform.startswith("linux")
        boot = _read(_PROC / "sys" / "kernel" / "random" / "boot_id") if linux else ""
        _current[pid] = Owner(sys.platform, socket.gethostname(), _pid_space() if linux else "",
                              boot, pid, process_start(pid) or "")
    return _current[pid]


def current_owner_text() -> str:
    """current_owner() as stored in an `owner_process` column (the column's default)."""
    return current_owner().dumps()


def owner_is_gone(owner: Owner | None, me: Owner | None = None,
                  probe: Callable[[int], str | None] = process_start) -> bool:
    """True only when the process `owner` describes has provably ended (rules in the module doc).
    `me` is the process asking (default: this one); `probe` is process_start."""
    if owner is None:
        return False
    me = me or current_owner()
    if (owner.system, owner.host) != (me.system, me.host):
        return False
    if owner.boot and me.boot and owner.boot != me.boot:
        return True
    if owner.space != me.space:
        return False
    start = probe(owner.pid)
    if start is None:
        return True
    return bool(owner.start and start and start != owner.start)
