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
_APP_NAME = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class AppInfo:
    workspace: str
    name: str
    title: str
    dist: Path

    @property
    def url_path(self) -> str:
        return f"/apps/{self.workspace}/{self.name}/"


def load_app(workspace_path: Path, name: str) -> AppInfo | None:
    """Load one app or return None. A problem with an existing folder logs a warning
    naming it; an invalid name logs nothing, since any URL can produce one."""
    if not _APP_NAME.fullmatch(name):
        return None
    app_path = apps_dir(workspace_path) / name
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
    dist = app_path / "dist"
    if not (dist / "index.html").is_file():
        logger.warning("app %s: dist/index.html not found — skipped", app_path)
        return None
    title = data.get("title")
    return AppInfo(
        workspace=workspace_path.name,
        name=name,
        title=str(title) if title else name,
        dist=dist,
    )


def discover_apps() -> list[AppInfo]:
    """All valid apps across workspaces, sorted by workspace then name."""
    found: list[AppInfo] = []
    for ws in list_workspaces():
        root = apps_dir(ws)
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "app.yaml").is_file():
                if not _APP_NAME.fullmatch(d.name):
                    logger.warning("app %s: folder name must match [A-Za-z0-9_-]+ — skipped", d)
                    continue
                info = load_app(ws, d.name)
                if info is not None:
                    found.append(info)
    return sorted(found, key=lambda a: (a.workspace, a.name))
