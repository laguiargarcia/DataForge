"""Static publication of workspace web apps: /apps/<workspace>/<app>/..."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from dataforge.apps import load_app
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


def _serve(file_path) -> FileResponse:
    media_type = _MEDIA_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    return FileResponse(file_path, media_type=media_type, headers=_NO_CACHE)


@router.get("/{workspace}/{app}")
def app_root(workspace: str, app: str):
    return RedirectResponse(f"/apps/{workspace}/{app}/", status_code=307, headers=_NO_CACHE)


@router.get("/{workspace}/{app}/{path:path}")
def app_file(workspace: str, app: str, path: str):
    if _is_unsafe_path(path):
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    try:
        ws_path = resolve_workspace(workspace)
    except ValueError:
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)
    info = load_app(ws_path, app)
    if info is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND, headers=_NO_CACHE)

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
