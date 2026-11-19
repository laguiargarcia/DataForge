"""The leak guard's main checkout: where its gitignored files (.leakguard-local, .leakguard-workspaces)
live, also when it runs from a worktree. Part of scripts/leakguard.py (df-n8ac.36)."""
import subprocess
from pathlib import Path


def _main_checkout(here):
    """(root of the main checkout of the repo at `here`, error). Gitignored files (.leakguard-local,
    .leakguard-workspaces) exist only there, never in a worktree. The error is set when `here` is a
    worktree whose main checkout cannot be found: checking without them would pass silently."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=here, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        common = ""
    if common and Path(common).is_dir():   # an old git echoes the unknown flag instead of failing
        return Path(common).parent, None
    if (here / ".git").is_file():
        return here, ("this is a worktree and its main checkout could not be found, so "
                      ".leakguard-local and .leakguard-workspaces were not read")
    return here, None


MAIN_ROOT, MAIN_ROOT_ERROR = _main_checkout(Path(__file__).resolve().parent.parent)
