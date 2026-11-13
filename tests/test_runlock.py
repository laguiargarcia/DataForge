"""Per-workspace run lock: jobs of one workspace run one at a time across processes."""
import errno
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dataforge.runlock import workspace_run_lock

_CHILD = """
import sys, time
from pathlib import Path
from dataforge.runlock import workspace_run_lock
ws, out, hold = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
with workspace_run_lock(ws, "child", poll_seconds=0.05):
    inicio = time.time()
    print("locked", flush=True)
    time.sleep(hold)
    fim = time.time()
out.write_text(f"{inicio} {fim}")
"""


@pytest.fixture
def ws(dataforge_home):
    path = dataforge_home / "workspaces" / "ws_a"
    path.mkdir()
    return path


def _spawn(ws: Path, out: Path, hold: float, read_stdout: bool = False) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(ws), str(out), str(hold)],
        stdout=subprocess.PIPE if read_stdout else subprocess.DEVNULL,
        text=True,
    )


def _interval(out: Path) -> tuple[float, float]:
    inicio, fim = out.read_text(encoding="utf-8").split()
    return float(inicio), float(fim)


def test_mesmo_workspace_nunca_sobrepoe(ws, tmp_path):
    outs = [tmp_path / "a.txt", tmp_path / "b.txt"]
    procs = [_spawn(ws, o, 1.0) for o in outs]
    for p in procs:
        assert p.wait(timeout=30) == 0
    first, second = sorted(_interval(o) for o in outs)
    assert first[1] <= second[0]


def test_workspaces_diferentes_rodam_juntos(dataforge_home, tmp_path):
    ws1 = dataforge_home / "workspaces" / "ws_a"
    ws2 = dataforge_home / "workspaces" / "ws_b"
    ws1.mkdir()
    ws2.mkdir()
    outs = [tmp_path / "a.txt", tmp_path / "b.txt"]
    procs = [_spawn(ws1, outs[0], 2.0), _spawn(ws2, outs[1], 2.0)]
    for p in procs:
        assert p.wait(timeout=30) == 0
    first, second = sorted(_interval(o) for o in outs)
    assert second[0] < first[1]


def test_processo_morto_libera(ws, tmp_path):
    proc = _spawn(ws, tmp_path / "x.txt", 60.0, read_stdout=True)
    try:
        assert proc.stdout.readline().strip() == "locked"
    finally:
        proc.kill()
        proc.wait()
        proc.stdout.close()
    start = time.monotonic()
    with workspace_run_lock(ws, "r1", poll_seconds=0.05):
        pass
    assert time.monotonic() - start < 5


def _enter_in_thread(ws: Path, run_id: str) -> tuple[threading.Event, threading.Event, threading.Thread]:
    entered, release = threading.Event(), threading.Event()

    def target():
        with workspace_run_lock(ws, run_id, poll_seconds=0.05):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return entered, release, t


def test_reentrante_mesmo_run_id(ws):
    with workspace_run_lock(ws, "r1", poll_seconds=0.05):
        entered, release, t = _enter_in_thread(ws, "r1")
        assert entered.wait(2)
        release.set()
        t.join(2)


def test_bloco_interno_nao_solta_o_externo(ws, tmp_path):
    with workspace_run_lock(ws, "r1", poll_seconds=0.05):
        with workspace_run_lock(ws, "r1", poll_seconds=0.05):
            pass
        proc = _spawn(ws, tmp_path / "c.txt", 0.1)
        try:
            time.sleep(1.0)
            assert proc.poll() is None  # still waiting for the lock
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    assert proc.wait(timeout=10) == 0


def test_run_id_diferente_espera(ws):
    with workspace_run_lock(ws, "r1", poll_seconds=0.05):
        entered, release, t = _enter_in_thread(ws, "r2")
        assert not entered.wait(0.7)
    assert entered.wait(5)
    release.set()
    t.join(2)


def test_erro_que_nao_e_trava_ocupada_nao_fica_em_laco(ws, monkeypatch):
    from dataforge import runlock

    def _broken(_fd):
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(runlock, "_try_lock", _broken)
    with pytest.raises(OSError) as info, workspace_run_lock(ws, "r1", poll_seconds=0.05):
        pass
    assert info.value.errno == errno.ENOLCK
    monkeypatch.undo()
    with workspace_run_lock(ws, "r2", poll_seconds=0.05):  # fd was closed, nothing held
        pass


def test_on_wait_uma_vez_enquanto_espera(ws):
    calls = []
    entered = threading.Event()

    def target():
        with workspace_run_lock(ws, "r2", poll_seconds=0.05, on_wait=lambda: calls.append(1)):
            entered.set()

    with workspace_run_lock(ws, "r1", poll_seconds=0.05):
        t = threading.Thread(target=target, daemon=True)
        t.start()
        assert not entered.wait(0.5)  # several polls while blocked
        assert calls == [1]
    assert entered.wait(5)
    t.join(2)
    assert calls == [1]


def test_on_wait_nunca_quando_livre(ws):
    calls = []
    with workspace_run_lock(ws, "r1", poll_seconds=0.05, on_wait=lambda: calls.append(1)), \
            workspace_run_lock(ws, "r1", poll_seconds=0.05, on_wait=lambda: calls.append(2)):  # reentrant
        pass
    assert calls == []
