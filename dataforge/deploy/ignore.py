from pathlib import Path
import pathspec


class DeployIgnore:
    """gitignore-semantics matcher for a workspace's deploy/.deployignore (law #3)."""

    # Universal non-artifacts excluded for every workspace and both deploy directions.
    # Applied BEFORE the workspace file, so a workspace can still re-include with '!'.
    DEFAULT_PATTERNS = ("__pycache__/", "*.pyc", "*.pyo")

    def __init__(self, spec: pathspec.PathSpec):
        self._spec = spec

    @classmethod
    def load(cls, workspace_dir: Path) -> "DeployIgnore":
        path = workspace_dir / "deploy" / ".deployignore"
        lines = path.read_text().splitlines() if path.exists() else []
        return cls(pathspec.PathSpec.from_lines("gitwildmatch", [*cls.DEFAULT_PATTERNS, *lines]))

    def matches(self, relpath: str) -> bool:
        """relpath is POSIX, relative to the workspace root."""
        return self._spec.match_file(relpath)
