"""Rules of dataforge/liveness.py: a run row's owner is judged gone only on proof (df-bw7)."""
import dataclasses
import json
import os
import socket
import subprocess
import sys
import time

import pytest
from hypothesis import given, strategies as st

from dataforge import liveness
from dataforge.liveness import (Owner, current_owner, current_owner_text, owner_is_gone,
                                parse_owner, process_start)
from tests.process_owners import ended_pid

_SUPPORTED = sys.platform == "win32" or sys.platform.startswith("linux")
needs_probe = pytest.mark.skipif(not _SUPPORTED, reason="process start times are read on Linux "
                                 "and Windows only; elsewhere no owner is ever judged gone")
_ME = Owner("linux", "box", "pid:[1]", "boot-a", 100, "500")


def _probe(result):
    return lambda pid: result


def _owner(**changes) -> Owner:
    return dataclasses.replace(_ME, **{"pid": 200, "start": "700", **changes})


# --- the decision -------------------------------------------------------------------------------

def test_a_row_without_an_owner_is_never_judged():
    """Rows written before owners were recorded (or unreadable) stay as they are."""
    assert owner_is_gone(None, _ME, _probe(None)) is False


@pytest.mark.parametrize("changes", [{"system": "win32"}, {"host": "other"}])
def test_an_owner_of_another_os_or_host_is_never_judged(changes):
    """WSL and native Windows share one database; a pid of one means nothing in the other."""
    assert owner_is_gone(_owner(**changes), _ME, _probe(None)) is False


def test_an_owner_from_an_earlier_boot_is_gone():
    """The kernel restarted since, so every process it ran has ended, even if the pid is live."""
    assert owner_is_gone(_owner(boot="boot-b"), _ME, _probe("700")) is True


@pytest.mark.parametrize("boot", ["", "boot-a"])
def test_an_unknown_or_equal_boot_proves_nothing(boot):
    assert owner_is_gone(_owner(boot=boot), _ME, _probe("700")) is False
    assert owner_is_gone(_owner(boot=boot), dataclasses.replace(_ME, boot=""), _probe("700")) is False


def test_an_owner_in_another_pid_namespace_is_never_judged():
    """Another WSL distro or a container on the same kernel: its pids are not ours."""
    assert owner_is_gone(_owner(space="pid:[2]"), _ME, _probe(None)) is False


def test_an_owner_with_no_process_left_is_gone():
    assert owner_is_gone(_owner(), _ME, _probe(None)) is True


def test_an_owner_whose_pid_now_runs_another_process_is_gone():
    """pid reuse: a live process with that pid but another start time is someone else."""
    assert owner_is_gone(_owner(), _ME, _probe("701")) is True


@pytest.mark.parametrize("recorded, now", [("700", "700"), ("", "701"), ("700", "")])
def test_a_live_pid_whose_start_matches_or_is_unknown_is_alive(recorded, now):
    assert owner_is_gone(_owner(start=recorded), _ME, _probe(now)) is False


def test_the_probe_is_asked_about_the_owners_pid():
    asked = []
    owner_is_gone(_owner(), _ME, lambda pid: asked.append(pid) or "700")
    assert asked == [200]


_text = st.text(alphabet="ab", max_size=2)
_owners = st.builds(Owner, _text, _text, _text, _text, st.integers(1, 3), _text)


@given(_owners, _owners, st.sampled_from([None, "", "a", "b"]))
def test_no_owner_of_another_os_or_host_is_ever_judged_gone(owner, me, now):
    if (owner.system, owner.host) != (me.system, me.host):
        assert owner_is_gone(owner, me, _probe(now)) is False


@given(_owners, _owners)
def test_an_owner_whose_process_still_runs_is_never_gone_in_its_own_boot(owner, me):
    """Same boot (or unknown) and the pid still runs with the recorded start: alive."""
    if not (owner.boot and me.boot and owner.boot != me.boot):
        assert owner_is_gone(owner, me, _probe(owner.start)) is False


# --- recording ----------------------------------------------------------------------------------

@given(_owners)
def test_an_owner_survives_its_stored_form(owner):
    assert parse_owner(owner.dumps()) == owner


@pytest.mark.parametrize("text", [
    None, "", "not json", "[]", "1",
    json.dumps({**dataclasses.asdict(_ME), "extra": 1}),
    json.dumps({k: v for k, v in dataclasses.asdict(_ME).items() if k != "start"}),
    json.dumps({**dataclasses.asdict(_ME), "pid": "100"}),
    json.dumps({**dataclasses.asdict(_ME), "start": 500}),
    json.dumps({**dataclasses.asdict(_ME), "pid": 0}),
    json.dumps({**dataclasses.asdict(_ME), "pid": -3}),
])
def test_an_unreadable_owner_is_no_owner(text):
    assert parse_owner(text) is None


def test_this_process_is_recorded_as_itself():
    me = current_owner()
    assert (me.system, me.host, me.pid) == (sys.platform, socket.gethostname(), os.getpid())
    assert json.loads(current_owner_text()) == dataclasses.asdict(me)
    assert owner_is_gone(me) is False


def test_the_owner_is_computed_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(liveness, "_current", {})
    monkeypatch.setattr(liveness, "process_start", lambda pid: calls.append(pid) or "9")
    assert current_owner() is current_owner()
    assert calls == [os.getpid()]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="boot id and pid namespace are Linux's")
def test_on_linux_the_boot_and_pid_namespace_are_recorded():
    me = current_owner()
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
        assert me.boot == f.read().strip()
    assert me.space == os.readlink("/proc/self/ns/pid")


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="Linux records them")
def test_elsewhere_no_boot_or_pid_namespace_is_recorded():
    assert (current_owner().boot, current_owner().space) == ("", "")


def test_an_unknown_start_is_recorded_empty(monkeypatch):
    monkeypatch.setattr(liveness, "_current", {})
    monkeypatch.setattr(liveness, "process_start", lambda pid: None)
    assert current_owner().start == ""


# --- probing real processes ---------------------------------------------------------------------

@needs_probe
def test_this_process_start_is_what_its_rows_record():
    assert process_start(os.getpid()) == current_owner().start != ""


@needs_probe
def test_a_live_child_is_alive_until_it_ends():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        owner = dataclasses.replace(current_owner(), pid=child.pid, start=process_start(child.pid))
        assert owner.start
        assert owner_is_gone(owner) is False
    finally:
        child.kill()
        child.wait()
    assert owner_is_gone(owner) is True


@needs_probe
def test_an_ended_process_has_no_start():
    assert process_start(ended_pid()) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux zombie processes")
def test_a_killed_child_nobody_waited_for_is_gone():
    """On Linux an exited child stays in the process table (state Z) until its parent waits."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    start = process_start(child.pid)
    child.kill()
    deadline = time.monotonic() + 10
    while process_start(child.pid) is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        assert start
        assert process_start(child.pid) is None
    finally:
        child.wait()


def test_an_unsupported_platform_never_reports_a_process_gone(monkeypatch):
    pid = ended_pid()
    monkeypatch.setattr(liveness.sys, "platform", "darwin")
    assert process_start(pid) == ""


# --- the Linux /proc reader, on a fake /proc ----------------------------------------------------

def _fake_proc(tmp_path, monkeypatch, stat: str | None):
    monkeypatch.setattr(liveness, "_PROC", tmp_path)
    if stat is not None:
        (tmp_path / "7").mkdir()
        (tmp_path / "7" / "stat").write_text(stat, encoding="utf-8")


def _stat(state: str, name: str = "python") -> str:
    # fields 3.. after the name; field 22 (starttime) is 4242
    rest = [state] + [str(n) for n in range(4, 22)] + ["4242", "23", "24"]
    return f"7 ({name}) " + " ".join(rest) + "\n"


@pytest.mark.parametrize("name", ["python", "a b", "x) S 1 (y", ")"])
def test_the_start_is_field_22_whatever_the_command_name(tmp_path, monkeypatch, name):
    _fake_proc(tmp_path, monkeypatch, _stat("S", name))
    assert liveness._linux_start(7) == "4242"


@pytest.mark.parametrize("state", ["Z", "X"])
def test_an_exited_process_not_yet_reaped_is_gone(tmp_path, monkeypatch, state):
    _fake_proc(tmp_path, monkeypatch, _stat(state))
    assert liveness._linux_start(7) is None


def test_no_proc_entry_means_no_process(tmp_path, monkeypatch):
    _fake_proc(tmp_path, monkeypatch, None)
    assert liveness._linux_start(7) is None


def test_an_unreadable_proc_entry_means_alive_start_unknown(tmp_path, monkeypatch):
    _fake_proc(tmp_path, monkeypatch, None)
    (tmp_path / "7" / "stat").mkdir(parents=True)   # reading a directory fails with an OSError
    assert liveness._linux_start(7) == ""


def test_missing_boot_id_and_namespace_are_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(liveness, "_PROC", tmp_path)
    assert liveness._read(tmp_path / "sys" / "kernel" / "random" / "boot_id") == ""
    assert liveness._pid_space() == ""


# --- the Windows reader, on a fake kernel32 (the real one is probed by the tests above) ---------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the Windows process reader")


class _Kernel32:
    """Stands in for kernel32: OpenProcess gives `handle`, the process exited with `exit_code`."""

    def __init__(self, handle=11, exit_code=259, queries_ok=True):
        self.handle, self.exit_code, self.queries_ok = handle, exit_code, queries_ok
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def GetExitCodeProcess(self, handle, code):
        code._obj.value = self.exit_code
        return self.queries_ok

    def GetProcessTimes(self, handle, created, *_):
        created._obj.dwHighDateTime, created._obj.dwLowDateTime = 1, 2
        return self.queries_ok

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


@windows_only
@pytest.mark.parametrize("error, expected", [(87, None), (5, "")])
def test_windows_a_pid_that_cannot_be_opened(monkeypatch, error, expected):
    """No such process (ERROR_INVALID_PARAMETER) is gone; access denied is alive, start unknown."""
    monkeypatch.setattr(liveness, "_kernel32", _Kernel32(handle=None))
    monkeypatch.setattr(liveness.ctypes, "get_last_error", lambda: error)
    assert liveness._windows_start(7) == expected


@windows_only
@pytest.mark.parametrize("kernel, expected", [
    (_Kernel32(), str(1 << 32 | 2)),             # running: its creation time
    (_Kernel32(exit_code=0), None),              # exited, a handle still open somewhere
    (_Kernel32(queries_ok=False), ""),           # cannot be queried: alive, start unknown
])
def test_windows_an_opened_process(monkeypatch, kernel, expected):
    monkeypatch.setattr(liveness, "_kernel32", kernel)
    assert liveness._windows_start(7) == expected
    assert kernel.closed == [11]
