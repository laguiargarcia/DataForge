"""AppTokenMiddleware in-process: the secret check every app backend adds in one line."""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dataforge.app_guard import TOKEN_ENV, TOKEN_HEADER, AppTokenMiddleware


def _guarded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AppTokenMiddleware)

    @app.get("/x")
    def x():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("oi")
        await websocket.close()

    return app


def test_com_segredo_no_ambiente(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "segredo")
    with TestClient(_guarded_app()) as client:  # "with" also runs the lifespan scope through the middleware
        assert client.get("/x", headers={TOKEN_HEADER: "segredo"}).json() == {"ok": True}
        wrong = client.get("/x", headers={TOKEN_HEADER: "errado"})
        assert wrong.status_code == 403
        assert wrong.json() == {"detail": "acesso só pelo DataForge"}
        assert client.get("/x").status_code == 403
        with client.websocket_connect("/ws", headers={TOKEN_HEADER: "segredo"}) as ws:
            assert ws.receive_text() == "oi"
        with pytest.raises(WebSocketDisconnect) as closed, \
                client.websocket_connect("/ws", headers={TOKEN_HEADER: "errado"}) as ws:
            ws.receive_text()
        assert closed.value.code == 1008


def test_sem_segredo_no_ambiente_recusa(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    client = TestClient(_guarded_app())
    assert client.get("/x").status_code == 403
    assert client.get("/x", headers={TOKEN_HEADER: ""}).status_code == 403  # fails closed
    with pytest.raises(WebSocketDisconnect) as closed, \
            client.websocket_connect("/ws", headers={TOKEN_HEADER: ""}) as ws:
        ws.receive_text()
    assert closed.value.code == 1008
