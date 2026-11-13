"""Server identity and ensure_server: find our DataForge on the port, or start it."""
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from dataforge import compat, instance
from dataforge.instance import Probe


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_client(body) -> httpx.Client:
    return _client(lambda request: httpx.Response(200, json=body))


def test_fingerprint_muda_quando_um_arquivo_muda(tmp_path: Path):
    a, b = tmp_path / "a.py", tmp_path / "sub" / "b.py"
    b.parent.mkdir()
    a.write_text("x = 1\n")
    b.write_text("y = 2\n")
    before = instance.code_fingerprint(tmp_path)
    assert instance.code_fingerprint(tmp_path) == before
    b.write_text("y = 22\n")
    assert instance.code_fingerprint(tmp_path) != before


def test_fingerprint_do_pacote_e_guardado():
    first = instance.code_fingerprint()
    assert first
    assert instance.code_fingerprint() is first   # the cached object, not a recomputed equal string


def test_health_payload(dataforge_home):
    payload = instance.health_payload()
    assert payload == {
        "status": "ok",
        "service": "dataforge",
        "version": instance.code_fingerprint(),
        "home": str(compat.data_dir().resolve()),
    }


def test_probe_nada_na_porta(dataforge_home):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    assert instance.probe(1, client=_client(refuse)) == (Probe.NONE, None)


def test_probe_resposta_sem_service(dataforge_home):
    assert instance.probe(1, client=_json_client({"status": "ok"}))[0] is Probe.OTHER_PROGRAM


def test_probe_resposta_que_nao_e_json(dataforge_home):
    client = _client(lambda request: httpx.Response(200, text="<html>"))
    assert instance.probe(1, client=client)[0] is Probe.OTHER_PROGRAM


def test_probe_status_de_erro_e_outro_programa(dataforge_home):
    client = _client(lambda request: httpx.Response(404, text="nope"))
    assert instance.probe(1, client=client)[0] is Probe.OTHER_PROGRAM


def test_probe_home_diferente(dataforge_home, tmp_path):
    body = instance.health_payload() | {"home": str(tmp_path / "outro")}
    kind, payload = instance.probe(1, client=_json_client(body))
    assert kind is Probe.OTHER_HOME
    assert payload == body


def test_probe_versao_diferente(dataforge_home):
    body = instance.health_payload() | {"version": "outra"}
    assert instance.probe(1, client=_json_client(body))[0] is Probe.OTHER_VERSION


def test_probe_igual(dataforge_home):
    assert instance.probe(1, client=_json_client(instance.health_payload()))[0] is Probe.OURS


def test_ensure_server_ours_devolve_a_porta(dataforge_home, monkeypatch):
    monkeypatch.setattr(instance, "probe", lambda port, timeout=1.0: (Probe.OURS, {}))
    monkeypatch.setattr(instance, "start_background", lambda: pytest.fail("não deve subir"))
    assert instance.ensure_server() == compat.api_port()


@pytest.mark.parametrize(
    ("kind", "payload", "message"),
    [
        (Probe.OTHER_PROGRAM, None, "a porta {port} está ocupada por outro programa; defina DATAFORGE_PORT"),
        (Probe.OTHER_HOME, {"home": "C:/x"}, "a porta {port} é de outro DataForge (C:/x)"),
        (Probe.OTHER_VERSION, {}, "há um DataForge de outra versão rodando; saia pela bandeja e abra de novo"),
    ],
)
def test_ensure_server_mensagens(dataforge_home, monkeypatch, kind, payload, message):
    monkeypatch.setattr(instance, "probe", lambda port, timeout=1.0: (kind, payload))
    monkeypatch.setattr(instance, "start_background", lambda: pytest.fail("não deve subir"))
    with pytest.raises(instance.InstanceError) as err:
        instance.ensure_server()
    assert str(err.value) == message.format(port=compat.api_port())


def test_ensure_server_sobe_quando_nao_ha(dataforge_home, monkeypatch):
    results = iter([Probe.NONE, Probe.NONE, Probe.OURS])
    started = []
    monkeypatch.setattr(instance, "probe", lambda port, timeout=1.0: (next(results), {}))
    monkeypatch.setattr(instance, "start_background", lambda: started.append(1))
    assert instance.ensure_server() == compat.api_port()
    assert started == [1]


def test_ensure_server_concorrente_sobe_um(dataforge_home, monkeypatch):
    started, finished = [], []

    def slow_start():
        time.sleep(0.3)  # long enough for the other thread to reach the probe/start decision
        started.append(1)
        finished.append(1)

    def fake_probe(port, timeout=1.0):
        return (Probe.OURS if finished else Probe.NONE), {}

    monkeypatch.setattr(instance, "probe", fake_probe)
    monkeypatch.setattr(instance, "start_background", slow_start)
    monkeypatch.setattr(instance, "_POLL_SECONDS", 0.01)
    ports, errors = [], []

    def run():
        try:
            ports.append(instance.ensure_server())
        except Exception as exc:  # noqa: BLE001 -- surface thread failures in the assert below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert len(ports) == 2
    assert started == [1]


def test_ensure_server_porta_invalida_vira_instance_error(dataforge_home, monkeypatch):
    monkeypatch.setenv("DATAFORGE_PORT", "abc")
    with pytest.raises(instance.InstanceError, match="DATAFORGE_PORT"):
        instance.ensure_server()


def test_ensure_server_timeout(dataforge_home, monkeypatch):
    monkeypatch.setattr(instance, "probe", lambda port, timeout=1.0: (Probe.NONE, None))
    monkeypatch.setattr(instance, "start_background", lambda: None)
    monkeypatch.setattr(instance, "_POLL_SECONDS", 0.01)
    with pytest.raises(instance.InstanceError, match="background.log"):
        instance.ensure_server(timeout=0.1)


def test_probe_timeout_conta_como_nada(dataforge_home):
    def silent(request):
        raise httpx.ReadTimeout("silent", request=request)

    assert instance.probe(1, client=_client(silent)) == (Probe.NONE, None)


def test_probe_ignora_proxy_do_ambiente(dataforge_home, monkeypatch):
    seen = {}

    class Spy(httpx.Client):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(instance.httpx, "Client", Spy)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    instance.probe(1)
    assert seen["trust_env"] is False


@pytest.fixture
def popen_calls(dataforge_home, monkeypatch):
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return object()

    monkeypatch.setattr(instance.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(instance, "_started", [])
    return calls


def test_start_background_posix_destacado(popen_calls, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    instance.start_background()
    (cmd, kwargs), = popen_calls
    assert cmd[1:] == ["-m", "dataforge", "background"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is instance.subprocess.DEVNULL
    assert (compat.data_dir() / "logs" / "background.log").exists()
    assert len(instance._started) == 1


def test_start_background_windows_sai_do_job(popen_calls, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    instance.start_background()
    (_, kwargs), = popen_calls
    flags = kwargs["creationflags"]
    assert flags & 0x08000000  # CREATE_NO_WINDOW
    assert flags & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    assert flags & 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
    assert kwargs["stdin"] is instance.subprocess.DEVNULL


def test_start_background_windows_job_proibe_breakaway(dataforge_home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(instance, "_started", [])
    flags_seen = []

    def fake_popen(cmd, **kwargs):
        flags_seen.append(kwargs["creationflags"])
        if kwargs["creationflags"] & 0x01000000:
            raise PermissionError("job forbids breakaway")
        return object()

    monkeypatch.setattr(instance.subprocess, "Popen", fake_popen)
    instance.start_background()
    assert len(flags_seen) == 2
    assert flags_seen[0] & 0x01000000
    assert not flags_seen[1] & 0x01000000
    assert flags_seen[1] & 0x08000000
