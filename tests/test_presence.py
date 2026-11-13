"""Window presence registry, its routes, the server job count and the shutdown request."""
import pytest

from dataforge import instance
from dataforge.api.routers import jobs as jobs_router
from dataforge.presence import EXPIRY_SECONDS, HEARTBEAT_SECONDS, PresenceRegistry


@pytest.fixture(autouse=True)
def clean_state():
    from dataforge.presence import registry
    registry.clear()
    instance.set_shutdown_handler(None)
    yield
    registry.clear()
    instance.set_shutdown_handler(None)


def test_valores_do_spec():
    assert (HEARTBEAT_SECONDS, EXPIRY_SECONDS) == (30, 120)


def test_touch_conta_e_expira():
    reg = PresenceRegistry(created_at=0.0)
    reg.touch("a", "admin", None, None, now=100.0)
    assert reg.count(100.0) == 1
    assert reg.count(100.0 + EXPIRY_SECONDS - 1) == 1
    assert reg.count(100.0 + EXPIRY_SECONDS) == 0


def test_touch_renova_a_validade():
    reg = PresenceRegistry(created_at=0.0)
    reg.touch("a", "admin", None, None, now=100.0)
    reg.touch("a", "admin", None, None, now=200.0)
    assert reg.count(200.0 + EXPIRY_SECONDS - 1) == 1


def test_leave_remove_e_e_idempotente():
    reg = PresenceRegistry(created_at=0.0)
    reg.touch("a", "admin", None, None, now=100.0)
    reg.leave("a")
    reg.leave("a")
    reg.leave("nunca-existiu")
    assert reg.count(100.0) == 0


def test_app_windows_conta_so_aquele_app():
    reg = PresenceRegistry(created_at=0.0)
    reg.touch("a1", "app", "ws", "dash", now=100.0)
    reg.touch("a2", "app", "ws", "dash", now=100.0)
    reg.touch("b", "app", "ws", "outro", now=100.0)
    reg.touch("c", "app", "outro_ws", "dash", now=100.0)
    reg.touch("adm", "admin", None, None, now=100.0)
    assert reg.app_windows("ws", "dash", 100.0) == 2
    assert reg.app_windows("ws", "outro", 100.0) == 1
    assert reg.app_windows("ws", "nada", 100.0) == 0
    assert reg.app_windows("ws", "dash", 100.0 + EXPIRY_SECONDS) == 0


def test_empty_since_none_enquanto_ha_janela():
    reg = PresenceRegistry(created_at=0.0)
    reg.touch("a", "admin", None, None, now=100.0)
    assert reg.empty_since(150.0) is None


def test_empty_since_com_saida_registrada(monkeypatch):
    reg = PresenceRegistry(created_at=0.0)
    monkeypatch.setattr("dataforge.presence.time.time", lambda: 300.0)
    reg.touch("a", "admin", None, None, now=100.0)
    reg.leave("a")
    assert reg.empty_since(400.0) == 300.0


def test_empty_since_quando_a_ultima_expirou():
    reg = PresenceRegistry(created_at=0.0)
    reg.touch("a", "admin", None, None, now=100.0)
    reg.touch("b", "admin", None, None, now=130.0)
    assert reg.empty_since(100.0 + EXPIRY_SECONDS + 5) is None  # b still alive
    assert reg.empty_since(130.0 + EXPIRY_SECONDS + 5) == 130.0 + EXPIRY_SECONDS


def test_empty_since_sem_nunca_ter_tido_janela_e_a_criacao_do_registro():
    reg = PresenceRegistry(created_at=42.0)
    assert reg.empty_since(1000.0) == 42.0


def test_rota_presence_204(test_client):
    r = test_client.post("/api/instance/presence", json={"client_id": "x", "kind": "admin"})
    assert r.status_code == 204
    from dataforge.presence import registry
    import time
    assert registry.count(time.time()) == 1


def test_rota_presence_app_registra_janela(test_client):
    r = test_client.post(
        "/api/instance/presence", json={"client_id": "x", "kind": "app", "workspace": "ws", "app": "dash"}
    )
    assert r.status_code == 204
    from dataforge.presence import registry
    import time
    assert registry.app_windows("ws", "dash", time.time()) == 1


@pytest.mark.parametrize("body", [
    {"client_id": "x", "kind": "app"},
    {"client_id": "x", "kind": "app", "workspace": "ws"},
    {"client_id": "x", "kind": "app", "workspace": "../x", "app": "dash"},
    {"client_id": "x", "kind": "app", "workspace": "ws", "app": "a b"},
    {"client_id": "x", "kind": "outro"},
    {"kind": "admin"},
])
def test_rota_presence_invalida_422(test_client, body):
    assert test_client.post("/api/instance/presence", json=body).status_code == 422


def test_rota_delete_presence_e_idempotente(test_client):
    test_client.post("/api/instance/presence", json={"client_id": "x", "kind": "admin"})
    assert test_client.delete("/api/instance/presence/x").status_code == 204
    assert test_client.delete("/api/instance/presence/x").status_code == 204
    from dataforge.presence import registry
    import time
    assert registry.count(time.time()) == 0


def test_shutdown_sem_handler_409(test_client):
    r = test_client.post("/api/instance/shutdown")
    assert r.status_code == 409
    assert r.json() == {"detail": "este servidor não pode ser parado por aqui; use Ctrl+C"}


def test_shutdown_chama_handler_202(test_client):
    calls = []
    instance.set_shutdown_handler(lambda: calls.append(1))
    assert test_client.post("/api/instance/shutdown").status_code == 202
    assert calls == [1]


def test_shutdown_recusa_com_job_rodando(test_client):
    calls = []
    instance.set_shutdown_handler(lambda: calls.append(1))
    with instance.track_server_job():
        r = test_client.post("/api/instance/shutdown")
    assert r.status_code == 409
    assert r.json() == {"detail": "há job rodando"}
    assert calls == []


def test_shutdown_force_chama_handler(test_client):
    calls = []
    instance.set_shutdown_handler(lambda: calls.append(1))
    with instance.track_server_job():
        r = test_client.post("/api/instance/shutdown?force=true")
    assert r.status_code == 202
    assert calls == [1]


def test_request_shutdown_sem_handler_devolve_false():
    assert instance.request_shutdown() is False
    instance.set_shutdown_handler(lambda: None)
    assert instance.request_shutdown() is True


def test_track_server_job_conta_aninhado_e_libera_na_excecao():
    assert instance.server_jobs() == 0
    with instance.track_server_job(), instance.track_server_job():
        assert instance.server_jobs() == 2
    with pytest.raises(RuntimeError), instance.track_server_job():
        raise RuntimeError("x")
    assert instance.server_jobs() == 0


def test_job_disparado_conta_ate_terminar(test_client, workspace_dir, simple_job_dir, monkeypatch):
    seen = []
    real = jobs_router.run_job

    def spy(*args, **kwargs):
        seen.append(instance.server_jobs())
        return real(*args, **kwargs)

    monkeypatch.setattr(jobs_router, "run_job", spy)
    r = test_client.post(f"/api/workspaces/{workspace_dir.name}/jobs/test_job/run")
    assert r.status_code == 202
    assert seen == [1]
    assert instance.server_jobs() == 0


def test_job_que_falha_tambem_libera_a_contagem(dataforge_home, workspace_dir, monkeypatch):
    import asyncio

    def _raise(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_router, "load_job", _raise)
    from dataforge.compat import db_url
    asyncio.run(jobs_router._run_job_background(
        "r1", "boom", workspace_dir, db_url(workspace_dir / "dataforge.db"),
        db_url(dataforge_home / "dataforge.db"), workspace_dir.name))
    assert instance.server_jobs() == 0


def test_begin_end_server_job_pareados():
    instance.begin_server_job()
    assert instance.server_jobs() == 1
    instance.end_server_job()
    assert instance.server_jobs() == 0


def test_rota_conta_o_job_antes_da_tarefa_rodar(dataforge_home, workspace_dir, simple_job_dir):
    import asyncio

    from fastapi import BackgroundTasks

    tasks = BackgroundTasks()  # never executed: the job has not started
    response = asyncio.run(jobs_router.trigger_job(workspace_dir.name, "test_job", tasks))
    try:
        assert response.status_code == 202
        assert instance.server_jobs() == 1
    finally:
        instance.end_server_job()
    assert instance.server_jobs() == 0


def test_rota_desfaz_a_contagem_se_add_task_falha(dataforge_home, workspace_dir, simple_job_dir):
    import asyncio

    class Broken:
        def add_task(self, *_a, **_k):
            raise RuntimeError("no queue")

    with pytest.raises(RuntimeError):
        asyncio.run(jobs_router.trigger_job(workspace_dir.name, "test_job", Broken()))
    assert instance.server_jobs() == 0


def test_job_contado_pela_rota_libera_uma_vez_ao_terminar(dataforge_home, workspace_dir, monkeypatch):
    import asyncio

    from dataforge.compat import db_url

    def _raise(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_router, "load_job", _raise)
    instance.begin_server_job()  # what the route did
    asyncio.run(jobs_router._run_job_background(
        "r2", "boom", workspace_dir, db_url(workspace_dir / "dataforge.db"),
        db_url(dataforge_home / "dataforge.db"), workspace_dir.name, counted=True))
    assert instance.server_jobs() == 0
