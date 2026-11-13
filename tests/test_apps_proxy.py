"""Proxy /apps/<ws>/<app>/api/... to the app backend, and the backend lifecycle in the server."""
import asyncio
import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from dataforge import api as api_module
from dataforge.api import app
from dataforge.api.routers.apps import close_client
from dataforge.app_backends import supervisor
from dataforge.app_guard import TOKEN_HEADER


@pytest.fixture(autouse=True)
def _limpa_backends():
    yield
    supervisor.stop_all()


@pytest.fixture
def client(dataforge_home):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def backend(workspace_dir, make_backend_app):
    return make_backend_app(workspace_dir)


def test_proxy_ida_e_volta(client, backend):
    r = client.post("/apps/test_ws/painel/api/eco?a=1&a=2", content=json.dumps({"x": 1}),
                    headers={"x-teste": "oi", "content-type": "application/json"})
    assert r.status_code == 200
    got = r.json()
    assert got["method"] == "POST"
    assert got["query"] == "a=1&a=2"
    assert json.loads(got["body"]) == {"x": 1}
    assert got["headers"]["x-teste"] == "oi"
    assert "x-dataforge-app-token" in got["headers"]


def test_proxy_corpo_binario_ida_e_volta(client, backend, workspace_dir):
    (backend.app_dir / "backend" / "main.py").write_text(
        (backend.app_dir / "backend" / "main.py").read_text()
        + "\nfrom fastapi.responses import Response\n"
          "@app.post('/bin')\nasync def bin_(request: Request):\n"
          "    return Response(await request.body(), media_type='application/octet-stream')\n",
        encoding="utf-8")
    data = os.urandom(2 * 1024 * 1024)
    r = client.post("/apps/test_ws/painel/api/bin", content=data)
    assert r.status_code == 200
    assert r.content == data


def test_segredo_do_cliente_e_substituido(client, backend):
    r = client.get("/apps/test_ws/painel/api/eco", headers={TOKEN_HEADER: "falso"})
    assert r.status_code == 200
    assert r.json()["headers"][TOKEN_HEADER] != "falso"


def test_app_sem_backend_api_404(client, workspace_dir, make_app):
    make_app(workspace_dir, "estatico")
    assert client.get("/apps/test_ws/estatico/api/eco").status_code == 404
    assert client.get("/apps/test_ws/nao_existe/api/eco").status_code == 404
    assert client.get("/apps/test_ws/estatico/api/..%5Cx").status_code == 404


def test_backend_quebrado_503_com_log(client, backend):
    (backend.app_dir / "backend" / "main.py").write_text("def (:\n", encoding="utf-8")
    r = client.get("/apps/test_ws/painel/api/eco")
    assert r.status_code == 503
    body = r.json()
    assert body["detail"]
    assert body["log"].endswith("app_painel.log")


def test_servidor_continua_de_pe_depois_do_503(client, backend):
    (backend.app_dir / "backend" / "main.py").write_text("def (:\n", encoding="utf-8")
    assert client.get("/apps/test_ws/painel/api/eco").status_code == 503
    assert client.get("/health").status_code == 200


def test_backend_parado_no_meio_vira_503(client, backend, monkeypatch):
    async def morto(info):
        return 1, "segredo"  # nothing listens on port 1
    monkeypatch.setattr(supervisor, "target", morto)
    r = client.get("/apps/test_ws/painel/api/eco")
    assert r.status_code == 503
    assert r.json()["log"].endswith("app_painel.log")


def test_host_origin_vale_no_proxy(client, backend):
    r = client.post("/apps/test_ws/painel/api/eco", content=b"x", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_estatico_continua(client, backend):
    r = client.get("/apps/test_ws/painel/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_lifespan_varre_e_para_tudo(dataforge_home, monkeypatch):
    calls = {"sweep": 0, "stop_all": 0}

    def sweep(now, presence):
        calls["sweep"] += 1
        return []

    monkeypatch.setattr(api_module, "SWEEP_SECONDS", 0.02)
    monkeypatch.setattr(supervisor, "sweep", sweep)
    monkeypatch.setattr(supervisor, "stop_all", lambda: calls.__setitem__("stop_all", calls["stop_all"] + 1))
    with TestClient(app):
        deadline = time.time() + 5
        while calls["sweep"] < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert calls["sweep"] >= 2
        assert calls["stop_all"] == 0
    assert calls["stop_all"] == 1


def test_varredura_que_falha_nao_derruba_a_tarefa(dataforge_home, monkeypatch):
    calls = {"n": 0}

    def sweep(now, presence):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(api_module, "SWEEP_SECONDS", 0.02)
    monkeypatch.setattr(supervisor, "sweep", sweep)
    with TestClient(app):
        deadline = time.time() + 5
        while calls["n"] < 2 and time.time() < deadline:
            time.sleep(0.02)
    assert calls["n"] >= 2


_EXTRA_BACKEND = '''
import gzip as _gzip
from fastapi.responses import Response as _R


@app.get("/cabecalhos")
async def cabecalhos(request: Request):
    return {"raw": [[k.decode("latin-1"), v.decode("latin-1")] for k, v in request.headers.raw]}


@app.get("/caminho")
async def caminho(request: Request):
    return {"raw_path": request.scope["raw_path"].decode("latin-1"), "query": request.url.query}


@app.get("/resposta")
def resposta():
    r = _R(b"ok")
    r.raw_headers.append((b"x-bytes", "Jos\\u00e9".encode("utf-8")))
    r.raw_headers.append((b"set-cookie", b"a=1"))
    r.raw_headers.append((b"set-cookie", b"b=2"))
    r.raw_headers.append((b"keep-alive", b"timeout=5"))
    r.raw_headers.append((b"x-quente", b"1"))
    r.raw_headers.append((b"connection", b"x-quente"))
    return r


@app.get("/gz")
def gz():
    return _R(_gzip.compress(b"corpo legivel"), headers={"content-encoding": "gzip"})


@app.get("/morre")
def morre():
    os._exit(1)


@app.api_route("/{rest:path}", methods=["GET"])
async def qualquer(rest: str, request: Request):
    return {"raw_path": request.scope["raw_path"].decode("latin-1")}
'''


@pytest.fixture
def backend2(backend):
    main = backend.app_dir / "backend" / "main.py"
    main.write_text(main.read_text() + _EXTRA_BACKEND, encoding="utf-8")
    return backend


def _raw_headers(r):
    return {k: v for k, v in r.json()["raw"]}


def _asgi_get(path, headers):
    """Raw-bytes round trip: Starlette's TestClient decodes headers to str, so it cannot carry these."""
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            try:
                return await c.get(path, headers=headers)
            finally:
                await close_client()  # the shared client belongs to this short-lived loop
    return asyncio.run(go())


def test_cabecalhos_bytes_e_filtros(dataforge_home, backend2):
    r = _asgi_get("/apps/test_ws/painel/api/cabecalhos", [
        (b"x-nome", b"Jos\xe9"), (b"accept-encoding", b"br"),
        (b"proxy-connection", b"keep-alive"), (b"keep-alive", b"5"),
        (b"x-quente", b"1"), (b"connection", b"x-quente")])
    got = _raw_headers(r)
    assert got["x-nome"] == "Jos\xe9"  # same bytes (latin-1 view)
    assert "br" not in got.get("accept-encoding", "")
    for gone in ("proxy-connection", "keep-alive", "x-quente"):
        assert gone not in got


def test_caminho_e_query_chegam_como_foram(client, backend2):
    r = client.get("/apps/test_ws/painel/api/dia/2026-11-12T10:00")
    assert r.status_code == 200
    assert r.json()["raw_path"] == "/dia/2026-11-12T10:00"
    r = client.get("/apps/test_ws/painel/api/a;b=c@d,e+f")
    assert r.json()["raw_path"] == "/a;b=c@d,e+f"
    r = client.get("/apps/test_ws/painel/api/x%2Fy?q=%C3%A9&z=1%2B1")
    assert r.json()["raw_path"] == "/x%2Fy"
    r = client.get("/apps/test_ws/painel/api/caminho?q=%C3%A9&z=1%2B1")
    assert r.json()["query"] == "q=%C3%A9&z=1%2B1"


def test_api_sem_barra_vai_para_o_backend(client, backend2, workspace_dir, make_app):
    assert client.get("/apps/test_ws/painel/api").json()["raw_path"] == "/"
    assert client.post("/apps/test_ws/painel/api").status_code == 405  # backend's own answer
    make_app(workspace_dir, "estatico")
    assert client.get("/apps/test_ws/estatico/api").status_code == 404


def test_resposta_cabecalhos(dataforge_home, backend2):
    r = _asgi_get("/apps/test_ws/painel/api/resposta", [])
    raw = dict(r.headers.raw)
    assert raw[b"x-bytes"] == "Jos\u00e9".encode()  # byte-exact, non-ASCII
    assert [v for k, v in r.headers.raw if k == b"set-cookie"] == [b"a=1", b"b=2"]
    for gone in (b"keep-alive", b"x-quente"):
        assert gone not in raw
    names = [k for k, _ in r.headers.raw]
    assert b"date" not in names  # the backend's; the real server adds its own
    assert b"server" not in names


def test_resposta_gzip_chega_decodificada(client, backend2):
    r = client.get("/apps/test_ws/painel/api/gz")
    assert r.status_code == 200
    assert r.content == b"corpo legivel"
    assert "content-encoding" not in r.headers


def test_backend_que_morre_na_resposta_vira_503(client, backend2):
    r = client.get("/apps/test_ws/painel/api/morre")
    assert r.status_code == 503
    assert r.json()["log"].endswith("app_painel.log")
