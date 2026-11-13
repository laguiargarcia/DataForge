"""Backend supervisor (real uvicorn processes) and the secret-checking middleware."""
import asyncio
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from dataforge import app_backends, compat
from dataforge.app_backends import BackendSupervisor, BackendUnavailable, _Running
from dataforge.app_guard import TOKEN_HEADER
from dataforge.presence import PresenceRegistry


@pytest.fixture
def sup():
    s = BackendSupervisor()
    yield s
    s.stop_all()


@pytest.fixture
def info(workspace_dir, make_backend_app):
    return make_backend_app(workspace_dir)


@pytest.fixture
def fast_code_check(monkeypatch):
    """The code check runs every 0.3 s instead of 3 s (test_valores_do_spec pins the real value)."""
    monkeypatch.setattr(app_backends, "CODE_CHECK_SECONDS", 0.3)
    return 0.3


@pytest.fixture
def sleeper(sup, info):
    """A plain process registered as the app's backend: the sweep only needs a process to stop."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    now = time.time()
    sup._running[(info.workspace, info.name)] = _Running(process, 0, "t", "f", now, now)
    yield now
    if process.poll() is None:
        process.kill()
    process.wait(5)


def _kill_backend(sup, info):
    """Kill the backend as a crash would (portable: no SIGKILL on Windows)."""
    process = sup._running[(info.workspace, info.name)].process
    process.kill()
    process.wait(5)


def get(port, path, token=None):
    headers = {TOKEN_HEADER: token} if token else {}
    return httpx.get(f"http://127.0.0.1:{port}{path}", headers=headers, trust_env=False, timeout=10)


def run(coro):
    return asyncio.run(coro)


def test_valores_do_spec():
    assert (app_backends.START_TIMEOUT_SECONDS, app_backends.IDLE_SECONDS,
            app_backends.CODE_CHECK_SECONDS) == (20, 900, 3)


def test_chamada_direta_sem_segredo_e_403(sup, info):
    port, token = run(sup.target(info))
    direct = get(port, "/eco")
    assert direct.status_code == 403
    assert direct.json() == {"detail": "acesso só pelo DataForge"}
    assert get(port, "/eco", "errado").status_code == 403
    assert get(port, "/eco", token).status_code == 200


def test_ambiente_do_backend(sup, info, workspace_dir, dataforge_home):
    (workspace_dir / ".env").write_text("SEGREDO_DO_BANCO=x\n", encoding="utf-8")
    port, token = run(sup.target(info))
    env = get(port, "/env", token).json()
    assert env["DATAFORGE_HOME"] == str(dataforge_home)
    assert env["DATAFORGE_WORKSPACE"] == "test_ws"
    assert env["DATAFORGE_WORKSPACE_DIR"] == str(workspace_dir)
    assert env["DATAFORGE_API"] == f"http://127.0.0.1:{compat.api_port()}"
    assert env["DATAFORGE_APP_TOKEN"] == "***"
    assert env["SEGREDO_DO_BANCO"] is None


def test_backend_morto_sobe_de_novo(sup, info):
    port, token = run(sup.target(info))
    pid = get(port, "/pid", token).json()["pid"]
    _kill_backend(sup, info)
    port2, token2 = run(sup.target(info))
    assert get(port2, "/pid", token2).json()["pid"] != pid


def test_backend_que_nao_sobe(sup, info):
    (info.app_dir / "backend" / "main.py").write_text("def (:\n", encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(BackendUnavailable) as err:
        run(sup.target(info))
    assert time.monotonic() - start < 20
    assert err.value.log_path.exists()
    assert "SyntaxError" in err.value.log_path.read_text()
    assert err.value.reason


def test_codigo_novo_reinicia(sup, info, fast_code_check):
    port, token = run(sup.target(info))
    pid = get(port, "/pid", token).json()["pid"]
    main = info.app_dir / "backend" / "main.py"
    main.write_text(main.read_text() + "\n# mudou\n", encoding="utf-8")
    time.sleep(fast_code_check + 0.2)
    port2, token2 = run(sup.target(info))
    assert get(port2, "/pid", token2).json()["pid"] != pid


def test_chamadas_concorrentes_sobem_um_backend(sup, info):
    async def many():
        return await asyncio.gather(*(sup.target(info) for _ in range(5)))
    results = run(many())
    assert len(set(results)) == 1
    port, token = results[0]
    assert get(port, "/pid", token).status_code == 200


def test_sweep_para_quando_fecha_a_ultima_janela(sup, info, sleeper):
    reg = PresenceRegistry()
    now = sleeper
    reg.touch("w1", "app", info.workspace, info.name, now)
    assert sup.sweep(now, reg) == []
    reg.leave("w1")
    assert sup.sweep(now + 1, reg) == [(info.workspace, info.name)]
    assert sup.sweep(now + 2, reg) == []


def test_sem_janela_nunca_vista_so_para_por_ociosidade(sup, info, sleeper):
    reg = PresenceRegistry()
    now = sleeper
    assert sup.sweep(now, reg) == []
    assert sup.sweep(now + 901, reg) == [(info.workspace, info.name)]


def test_chamada_adia_a_ociosidade(sup, info, sleeper):
    reg = PresenceRegistry()
    now = sleeper
    sup.record_call(info.workspace, info.name, now + 800)
    assert sup.sweep(now + 901, reg) == []
    assert sup.sweep(now + 800 + 901, reg) == [(info.workspace, info.name)]


def test_stop_all_mata_processos(sup, info):
    port, token = run(sup.target(info))
    sup.stop_all()
    with pytest.raises(httpx.TransportError):
        get(port, "/pid", token)


def _slow_backend(info):
    (info.app_dir / "backend" / "main.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")


def test_arquivo_que_nao_e_py_tambem_reinicia(sup, info, fast_code_check):
    (info.app_dir / "backend" / "dados.json").write_text("{}", encoding="utf-8")
    port, token = run(sup.target(info))
    pid = get(port, "/pid", token).json()["pid"]
    (info.app_dir / "backend" / "dados.json").write_text('{"a": 1}', encoding="utf-8")
    time.sleep(fast_code_check + 0.2)
    port2, token2 = run(sup.target(info))
    assert get(port2, "/pid", token2).json()["pid"] != pid


def test_codigo_igual_nao_reinicia(sup, info, fast_code_check):
    port, token = run(sup.target(info))
    pid = get(port, "/pid", token).json()["pid"]
    time.sleep(fast_code_check + 0.2)
    port2, token2 = run(sup.target(info))
    assert (port2, token2) == (port, token)
    assert get(port2, "/pid", token2).json()["pid"] == pid


def test_pycache_nao_conta_como_mudanca(sup, info, fast_code_check):
    port, token = run(sup.target(info))
    (info.app_dir / "backend" / "__pycache__").mkdir(exist_ok=True)
    (info.app_dir / "backend" / "__pycache__" / "x.pyc").write_bytes(b"1")
    time.sleep(fast_code_check + 0.2)
    assert run(sup.target(info)) == (port, token)


def test_timeout_de_subida_mata_o_processo(sup, info, monkeypatch):
    _slow_backend(info)
    monkeypatch.setattr(app_backends, "START_TIMEOUT_SECONDS", 2)
    spawned = []
    real = app_backends._spawn
    monkeypatch.setattr(app_backends, "_spawn", lambda *a: spawned.append(real(*a)) or spawned[-1])
    with pytest.raises(BackendUnavailable) as err:
        run(sup.target(info))
    assert "2 s" in err.value.reason
    assert isinstance(err.value.log_path, Path)
    assert err.value.log_path == app_backends.log_path(info)
    assert spawned[0].poll() is not None


def test_subida_cancelada_nao_deixa_processo(sup, info, monkeypatch):
    _slow_backend(info)
    spawned = []
    real = app_backends._spawn
    monkeypatch.setattr(app_backends, "_spawn", lambda *a: spawned.append(real(*a)) or spawned[-1])

    async def scenario():
        task = asyncio.create_task(sup.target(info))
        await asyncio.sleep(1)  # long enough for the spawn, far shorter than the start timeout
        assert spawned
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(scenario())
    assert spawned[0].poll() is not None


def test_popen_com_oserror_vira_backend_unavailable(sup, info, monkeypatch):
    def boom(*a, **k):
        raise OSError("sem permissão")
    monkeypatch.setattr(app_backends.subprocess, "Popen", boom)
    with pytest.raises(BackendUnavailable) as err:
        run(sup.target(info))
    assert "sem permissão" in err.value.reason


def test_janela_vista_zera_ao_reiniciar(sup, info):
    reg = PresenceRegistry()
    run(sup.target(info))
    now = time.time()
    reg.touch("w1", "app", info.workspace, info.name, now)
    assert sup.sweep(now, reg) == []  # window seen
    _kill_backend(sup, info)
    run(sup.target(info))
    reg.leave("w1")
    assert sup.sweep(time.time(), reg) == []  # new start: no window seen yet
