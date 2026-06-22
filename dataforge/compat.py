import os
import re
from pathlib import Path
from typing import Optional
import platformdirs

_VALID_WORKSPACE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


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
        if not _VALID_WORKSPACE_NAME.match(name):
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


def global_db_url() -> str:
    """Return the SQLite URL for the global DataForge database."""
    return db_url(data_dir() / "dataforge.db")
