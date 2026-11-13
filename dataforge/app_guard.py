"""ASGI middleware for a workspace app backend: refuse calls that did not come through DataForge.

DataForge starts the backend with a per-start secret in ``DATAFORGE_APP_TOKEN`` and sends it in the
``x-dataforge-app-token`` header of every proxied call. Without the secret in the environment the
middleware refuses everything (fail closed).
"""
import hmac
import json
import os

TOKEN_HEADER = "x-dataforge-app-token"
TOKEN_ENV = "DATAFORGE_APP_TOKEN"

_DENIED = json.dumps({"detail": "acesso só pelo DataForge"}).encode()


class AppTokenMiddleware:
    def __init__(self, app) -> None:
        self.app = app
        self._token = os.environ.get(TOKEN_ENV, "").encode()  # noqa: TID251 -- the supervisor hands the secret over in the process environment

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        sent = b""
        for key, value in scope.get("headers", []):
            if key == TOKEN_HEADER.encode():
                sent = value
                break
        if self._token and hmac.compare_digest(sent, self._token):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(_DENIED)).encode()),
        ]})
        await send({"type": "http.response.body", "body": _DENIED})
