from pathlib import Path
import pathspec


class DeployIgnore:
    """gitignore-semantics matcher for a workspace's deploy/.deployignore (law #3)."""

    # Universal non-artifacts excluded for every workspace and both deploy directions.
    # Applied BEFORE the workspace file, so a workspace can still re-include with '!'.
    # node_modules/ and test-results/ are the web-app equivalents (apps/**, df-ve7): installed
    # dependencies and Playwright output. The compiled dist/ is a definition and does travel.
    DEFAULT_PATTERNS = ("__pycache__/", "*.pyc", "*.pyo", "node_modules/", "test-results/")

    def __init__(self, spec: pathspec.PathSpec, folded: pathspec.PathSpec | None = None):
        self._spec = spec
        self._folded = folded or spec   # the same patterns, casefolded (matches_any_case)

    @classmethod
    def load(cls, workspace_dir: Path) -> "DeployIgnore":
        path = workspace_dir / "deploy" / ".deployignore"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        patterns = [*cls.DEFAULT_PATTERNS, *lines]
        return cls(pathspec.GitIgnoreSpec.from_lines(patterns),
                   pathspec.GitIgnoreSpec.from_lines([p.casefold() for p in patterns]))

    def matches(self, relpath: str) -> bool:
        """relpath is POSIX, relative to the workspace root."""
        return self._spec.match_file(relpath)

    def matches_any_case(self, relpath: str) -> bool:
        """matches(), also ignoring case. Prune uses it: on NTFS 'CONFIG/x' is under 'config/', and
        on a case-sensitive disk it only keeps a file that could have been deleted (df-886.18)."""
        return self.matches(relpath) or self._folded.match_file(relpath.casefold())
