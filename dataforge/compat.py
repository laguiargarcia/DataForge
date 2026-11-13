import os
import re
import sys
from pathlib import Path
from typing import Optional
import platformdirs

# fullmatch, not ^...$: '$' also matches before a trailing newline, so 'Prod\n' passed (df-utq8).
_VALID_NAME = re.compile(r"[A-Za-z0-9_-]+")

ALLOWED_HOSTS_ENV = "DATAFORGE_ALLOWED_HOSTS"
PORT_ENV = "DATAFORGE_PORT"
DEFAULT_API_PORT = 8000
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def valid_name(name: object) -> bool:
    """True for a workspace, job or trigger name made only of ASCII letters, digits, '-' and '_'.
    These names go into paths and into OS scheduler commands (PowerShell, bash, crontab, launchd),
    so nothing else is ever let through (df-utq8)."""
    return isinstance(name, str) and _VALID_NAME.fullmatch(name) is not None


def data_dir() -> Path:
    """
    Return the DataForge data directory, creating it on first call.

    Priority:
      1. $DATAFORGE_HOME env var (operator-set)
      2. platformdirs.user_data_dir("DataForge") — platform-appropriate location
         Linux:   ~/.local/share/DataForge
         Windows: C:\\Users\\<user>\\AppData\\Local\\DataForge

    Prints a one-time notice to stdout on first creation.
    """
    env = os.environ.get("DATAFORGE_HOME")
    if env:
        path = Path(env)
    else:
        path = Path(platformdirs.user_data_dir("DataForge", appauthor=False))
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if created:
        print(f"Created DataForge data directory: {path}")
    return path


def set_data_dir(path: str | Path) -> None:
    """Point DataForge at ``path`` ($DATAFORGE_HOME) for this process and its children."""
    os.environ["DATAFORGE_HOME"] = str(Path(path).expanduser().resolve())


def set_api_port(port: int) -> None:
    """Make ``port`` the API port ($DATAFORGE_PORT) for this process and its children."""
    os.environ[PORT_ENV] = str(port)


def webengine_profile_dir() -> Path:
    """Folder of the persistent web profile of the app windows (cookies, localStorage...)."""
    path = data_dir() / "webengine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def locks_dir() -> Path:
    """Folder of the OS lock files (workspace run locks, server start), created on demand."""
    path = data_dir() / "locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """Folder of the logs of the detached DataForge processes (background, gui), created on demand."""
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_lock_path(workspace_dir: Path) -> Path:
    """Return the per-workspace run-lock file, creating its ``locks/`` directory.

    The lock lives in the ``locks/`` folder of the home that holds this workspace
    (``<home>/workspaces/<ws>`` -> ``<home>/locks/<ws>.lock``), which is ``locks_dir()``
    for every real workspace. Deriving it from the workspace path, not from
    $DATAFORGE_HOME, keeps a workspace outside the configured home (a test's tmp dir)
    from writing locks into that home.
    """
    folder = Path(workspace_dir).resolve().parent.parent / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{Path(workspace_dir).name}.lock"


def db_url(path: Path) -> str:
    """
    Return a cross-platform SQLite URL with forward slashes.

    Uses path.resolve().as_posix() to guarantee forward slashes on Windows,
    which SQLAlchemy requires for SQLite URLs.
    """
    return f"sqlite:///{path.resolve().as_posix()}"


def resolve_db(db: Optional[str], workspace_path: Path) -> str:
    """
    Resolve a SQLite URL for a given optional db name and workspace path.

    Uses provided db URL if given, otherwise constructs a forward-slash
    SQLite URL from workspace_path / "data" / "dataforge.db".
    """
    if db:
        return db
    data_dir = workspace_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return db_url(data_dir / "dataforge.db")


def resolve_workspace(name: Optional[str]) -> Path:
    """
    Resolve a workspace name to its filesystem path.

    Priority:
      1. If name is given: data_dir() / workspaces / <name>
      2. If name is None: check CWD contains workspace.yaml (backward-compat)

    Raises:
        ValueError: if name is None and CWD has no workspace.yaml
    """
    base = data_dir()
    if name is not None:
        if not valid_name(name):
            raise ValueError(
                f"Invalid workspace name '{name}': only alphanumerics, hyphens, and underscores are allowed."
            )
        path = base / "workspaces" / name
        if not (path / "workspace.yaml").exists():
            raise ValueError(f"Workspace '{name}' not found in {base / 'workspaces'}.")
        return path
    # CWD fallback — backward-compatible with existing behavior
    cwd = Path.cwd()
    if (cwd / "workspace.yaml").exists():
        return cwd
    raise ValueError(
        "Não está em um workspace. Use --workspace <nome> ou entre no diretório do workspace."
    )


def list_workspaces() -> list[Path]:
    """
    Return all workspace directories found under data_dir() / workspaces/.

    A valid workspace directory contains a workspace.yaml file.
    Returns an empty list if the workspaces/ directory does not exist.
    """
    workspaces_dir = data_dir() / "workspaces"
    if not workspaces_dir.is_dir():
        return []
    return sorted(p.parent for p in workspaces_dir.glob("*/workspace.yaml"))


def allowed_hosts() -> set[str] | None:
    """
    Return the host names the API answers to: loopback plus $DATAFORGE_ALLOWED_HOSTS
    (comma-separated). None means no Host check, when the variable contains `*`.
    """
    names = {n.strip().lower() for n in os.environ.get(ALLOWED_HOSTS_ENV, "").split(",")}
    names.discard("")
    if "*" in names:
        return None
    return _LOOPBACK_HOSTS | names


_MAX_PORT = 65535


def api_port() -> int:
    """Return the port of the local DataForge API: $DATAFORGE_PORT, else DEFAULT_API_PORT.
    Raises ValueError naming the variable when it is not a port number (df-yvgx)."""
    raw = os.environ.get(PORT_ENV)
    if raw is None:
        return DEFAULT_API_PORT
    port = int(raw) if raw.strip().isdigit() else 0
    if not 1 <= port <= _MAX_PORT:
        raise ValueError(f"{PORT_ENV} must be a port number (1-{_MAX_PORT}), got {raw!r}")
    return port


GRACE_ENV = "DATAFORGE_GRACE_SECONDS"


def grace_seconds(default: float) -> float:
    """Seconds the server stays up with no window and no job: $DATAFORGE_GRACE_SECONDS, else ``default``."""
    try:
        value = float(os.environ.get(GRACE_ENV, default))
    except ValueError:
        return default
    return value if value >= 0 else default


def force_offscreen_without_display() -> None:
    """On Linux with no X11/Wayland display, make Qt run headless (QT_QPA_PLATFORM=offscreen)."""
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"


def global_db_url() -> str:
    """Return the SQLite URL for the global DataForge database."""
    return db_url(data_dir() / "dataforge.db")


def apps_dir(workspace_path: Path) -> Path:
    """Return <workspace>/apps (the workspace's web apps). Does not create it."""
    return workspace_path / "apps"


def curated_dir(workspace_path: Path) -> Path:
    """Return <workspace>/data/curated. Does not create it."""
    return workspace_path / "data" / "curated"
