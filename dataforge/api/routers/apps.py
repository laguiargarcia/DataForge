"""Workspace web apps: static files at /apps/<workspace>/<app>/... and the backend proxy at .../api/..."""
import asyncio
import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from dataforge.app_backends import BackendUnavailable, log_path, supervisor
from dataforge.app_guard import TOKEN_HEADER
from dataforge.apps import AppInfo, load_app
from dataforge.compat import resolve_workspace

router = APIRouter()

_NO_CACHE = {"Cache-Control": "no-cache"}
_NOT_FOUND = "Not found"

# Fixed map: the OS mimetypes registry (e.g. Windows) can return wrong types for .js.
_MEDIA_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".map": "application/json",
}


def _is_unsafe_path(path: str) -> bool:
    """Windows reads these as drive, UNC or stream paths, and resolving a UNC path contacts its host."""
    return path.startswith("/") or any(c in path for c in ("\\", ":", "\0"))


def _find_app(workspace: str, app: str, path: str | None = None) -> AppInfo:
    """The app, or a 404 for an unsafe file path (None: not a file path), an invalid workspace or an unknown app."""
    if path is not None and _is_unsafe_path(path):
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    try:
        ws_path = resolve_workspace(workspace)
    except ValueError:
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    info = load_app(ws_path, app)
    if info is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    return info


def _serve(file_path) -> FileResponse:
    media_type = _MEDIA_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return FileResponse(file_path, media_type=media_type, headers=_NO_CACHE)


_HOP_BY_HOP = frozenset({b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
                         b"proxy-connection", b"te", b"trailer", b"trailers", b"transfer-encoding", b"upgrade"})
# accept-encoding: httpx may not decode what the browser accepts (br). content-length/-encoding: httpx
# already decoded the body, so what the backend declared no longer applies. date/server: uvicorn adds its own.
_NOT_FORWARDED_REQUEST = _HOP_BY_HOP | {b"host", b"content-length", b"accept-encoding", TOKEN_HEADER.encode()}
_NOT_FORWARDED_RESPONSE = _HOP_BY_HOP | {b"content-length", b"content-encoding", b"date", b"server"}
_API_INDEX = 4  # position of "api" in /apps/<workspace>/<app>/api/...
_API_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
_TIMEOUT = httpx.Timeout(300.0, connect=5.0)

_shared: dict = {}  # "loop" and "client" of the one shared client


def _shared_client() -> httpx.AsyncClient:
    """One client for all calls (reused connections); rebuilt only if the event loop changed."""
    loop = asyncio.get_running_loop()
    if _shared.get("loop") is not loop:
        _shared["client"] = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=_TIMEOUT)
        _shared["loop"] = loop
    return _shared["client"]


async def close_client() -> None:
    client = _shared.pop("client", None)
    _shared.pop("loop", None)
    if client is not None:
        await client.aclose()


def _unavailable(reason: str, log_path) -> JSONResponse:
    return JSONResponse({"detail": reason, "log": str(log_path)}, status_code=503)


def _forwarded(raw_headers, blocked: frozenset) -> list[tuple[bytes, bytes]]:
    """Header bytes to pass on: lowercase names, minus `blocked` and anything the Connection header names."""
    named = {token.strip().lower()
             for key, value in raw_headers if key.lower() == b"connection" for token in value.split(b",")}
    return [(key.lower(), value) for key, value in raw_headers
            if key.lower() not in blocked and key.lower() not in named]


def _backend_path(request: Request, path: str) -> str:
    """The path after /api/ exactly as the client sent it (no decode/re-encode)."""
    raw = request.scope.get("raw_path")
    if raw:
        parts = raw.split(b"/", _API_INDEX + 1)  # ['', apps, workspace, app, api, rest]
        if len(parts) > _API_INDEX:
            return b"".join(parts[_API_INDEX + 1:]).decode("latin-1")
    return quote(path)


@router.api_route("/{workspace}/{app}/api", methods=_API_METHODS)
@router.api_route("/{workspace}/{app}/api/{path:path}", methods=_API_METHODS)
async def app_api(request: Request, workspace: str, app: str, path: str = ""):
    info = _find_app(workspace, app)  # the path goes to the backend, not to the filesystem
    if not info.backend:
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    try:
        port, secret = await supervisor.target(info)
    except BackendUnavailable as err:
        return _unavailable(err.reason, err.log_path)
    supervisor.record_call(info.workspace, info.name, time.time())

    url = f"http://127.0.0.1:{port}/{_backend_path(request, path)}"
    query = request.scope.get("query_string", b"").decode("latin-1")
    if query:
        url += f"?{query}"
    headers = _forwarded(request.headers.raw, _NOT_FORWARDED_REQUEST)
    headers.append((TOKEN_HEADER.encode(), secret.encode()))
    try:
        upstream = await _shared_client().request(
            request.method, url, headers=headers, content=await request.body())
    except httpx.TimeoutException:
        return JSONResponse({"detail": "o backend do app não respondeu a tempo"}, status_code=504)
    except httpx.TransportError:  # refused (stopped by a sweep), dropped mid-answer, dead pooled socket
        return _unavailable("o backend do app parou de responder; tente de novo", log_path(info))

    response = Response(upstream.content, status_code=upstream.status_code)
    response.raw_headers.extend(_forwarded(upstream.headers.raw, _NOT_FORWARDED_RESPONSE))  # repeats survive
    return response


@router.get("/{workspace}/{app}")
def app_root(workspace: str, app: str):
    return RedirectResponse(f"/apps/{workspace}/{app}/", status_code=307, headers=_NO_CACHE)


@router.get("/{workspace}/{app}/{path:path}")
def app_file(workspace: str, app: str, path: str):
    info = _find_app(workspace, app, path)

    dist = info.dist.resolve()
    target = (dist / path).resolve()
    if not target.is_relative_to(dist):
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    if target.is_file():
        return _serve(target)
    last_segment = path.rsplit("/", 1)[-1]
    if "." in last_segment:
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    return _serve(dist / "index.html")
