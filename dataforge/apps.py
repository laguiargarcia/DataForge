"""Discovery of workspace web apps (<workspace>/apps/<name>/{app.yaml,dist/})."""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from dataforge.compat import apps_dir, list_workspaces

logger = logging.getLogger(__name__)

# Same rule as workspace names. `name` comes from the URL: on Windows a drive or UNC
# value would replace the base path, so it is checked before any disk access.
_APP_NAME = re.compile(r"[\w-]+", re.ASCII)
# `backend` is an ASGI object as `module:object`, never a command line.
_BACKEND = re.compile(r"[A-Za-z_][\w.]*:[A-Za-z_]\w*", re.ASCII)


@dataclass(frozen=True)
class AppInfo:
    workspace: str
    name: str
    title: str
    dist: Path
    app_dir: Path
    icon: Path | None = None
    backend: str | None = None

    @property
    def url_path(self) -> str:
        return f"/apps/{self.workspace}/{self.name}/"


def _resolve_icon(app_path: Path, value: object) -> Path | None:
    """The icon file, resolved and confined to the app folder; None (with a warning) if
    it is missing, not a path, or escapes the folder."""
    if not isinstance(value, str) or not value.strip():
        logger.warning("app %s: icon must be a path string — ignored", app_path)
        return None
    root = app_path.resolve()
    icon = (root / value).resolve()
    if not icon.is_relative_to(root):
        logger.warning("app %s: icon %r is outside the app folder — ignored", app_path, value)
        return None
    if not icon.is_file():
        logger.warning("app %s: icon %r not found — ignored", app_path, value)
        return None
    return icon


def _read_manifest(app_path: Path, name: str) -> dict | None:
    """The parsed app.yaml if it is a mapping naming this folder, else None (warned)."""
    try:
        data = yaml.safe_load((app_path / "app.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        logger.warning("app %s: cannot read app.yaml (%s) — skipped", app_path, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("app %s: app.yaml must be a mapping — skipped", app_path)
        return None
    if data.get("name") != name:
        logger.warning("app %s: name %r does not match directory %r — skipped",
                       app_path, data.get("name"), name)
        return None
    return data


def load_app(workspace_path: Path, name: str) -> AppInfo | None:
    """Load one app or return None. A problem with an existing folder logs a warning
    naming it; an invalid name logs nothing, since any URL can produce one."""
    if not _APP_NAME.fullmatch(name):
        return None
    app_path = apps_dir(workspace_path) / name
    data = _read_manifest(app_path, name)
    if data is None:
        return None
    dist = app_path / "dist"
    if not (dist / "index.html").is_file():
        logger.warning("app %s: dist/index.html not found — skipped", app_path)
        return None
    backend = data.get("backend")
    if backend is not None and not (isinstance(backend, str) and _BACKEND.fullmatch(backend)):
        logger.warning("app %s: backend %r must look like module:object — skipped",
                       app_path, backend)
        return None
    icon = _resolve_icon(app_path, data["icon"]) if data.get("icon") is not None else None
    title = data.get("title")
    return AppInfo(
        workspace=workspace_path.name,
        name=name,
        title=str(title) if title else name,
        dist=dist,
        app_dir=app_path,
        icon=icon,
        backend=backend,
    )


def _workspace_apps(ws: Path) -> list[AppInfo]:
    """The valid apps of one workspace: each folder of apps/ that holds an app.yaml."""
    root = apps_dir(ws)
    if not root.is_dir():
        return []
    found: list[AppInfo] = []
    for d in sorted(root.iterdir()):
        if not (d.is_dir() and (d / "app.yaml").is_file()):
            continue
        if not _APP_NAME.fullmatch(d.name):
            logger.warning("app %s: folder name must match [A-Za-z0-9_-]+ — skipped", d)
            continue
        info = load_app(ws, d.name)
        if info is not None:
            found.append(info)
    return found


def discover_apps() -> list[AppInfo]:
    """All valid apps across workspaces, sorted by workspace then name."""
    found = [info for ws in list_workspaces() for info in _workspace_apps(ws)]
    return sorted(found, key=lambda a: (a.workspace, a.name))
