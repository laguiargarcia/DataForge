"""The leak guard's files: which paths are private, how a file and a path are read and checked, and
what git says (the files .gitignore excludes, every tracked file, the whole history). Part of
scripts/leakguard.py (df-n8ac.36), whose docstring describes the whole guard."""
import os
import subprocess
from pathlib import Path

from scripts.leakguard_patterns import _mask, _pattern_failures
from scripts.leakguard_values import _real_value_failures

# Surfaces of this repo that are never published.
BLOCK_PREFIXES = (
    "workspaces/GetFinance-Dev/",
    ".planning/",
    ".beads/",
    "tests/workspaces/",                 # pipeline tests that read real workspaces
    "tests/test_finance_daily_dag.py",   # same
    "tests/test_testloop_evals.py",      # tests of the private eval harness
    "docs/",       # D9: docs never committed to public repo
)
# Inside a workspace (workspaces/<name>/...): where its real data and its real config live.
WORKSPACE_PRIVATE_DIRS = ("data/", "report/", "docs/archive/")
WORKSPACE_PRIVATE_FILES = {"docs/workbook.md", "config/investments.yaml",
                           "config/merchant_normalization.local.yaml"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".duckdb", ".parquet", ".xlsx", ".xlsm", ".xls"}
# Inside a workspace, an archive is a copy of its data (backup_dados.py zips data/raw and edicao.db),
# whatever the name; it is judged by its path, since its content cannot be read.
WORKSPACE_ARCHIVE_SUFFIXES = (".zip", ".7z", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".tar", ".rar")
# Inside a workspace, any other binary file is refused too (a dump under any name), except a built
# app's web fonts and images: public assets that hold no record.
PUBLIC_BINARY_SUFFIXES = {".woff", ".woff2", ".ttf", ".otf", ".eot", ".png", ".jpg", ".jpeg", ".gif",
                          ".ico", ".webp"}
PRIVATE_FILE = "private file (data, report, .env, real config, a data artifact or gitignored)"
BINARY_IN_WORKSPACE = "binary file inside a workspace (a dump or a backup cannot be read for real values)"


def check_text(rel, text, sources, where=None):
    """Every finding in `text`, located by `where(line)` (by default `rel:line`)."""
    where = where or (lambda n: f"{rel}:{n}")
    return _pattern_failures(rel, text, where) + _real_value_failures(rel, text, sources, where)


def _path_failures(rel, sources):
    """(the path as findings may show it, the findings in the path itself). A path with a finding in it
    is shown masked: CI logs are public, and a file named after a document would publish it."""
    failures = check_text(rel, rel, sources, where=lambda _: f"the path {_mask(rel)}")
    return (_mask(rel) if failures else rel), failures


def _inside_workspace(rel):
    """The path of `rel` inside its workspace (workspaces/<name>/...), lowercased; None outside one."""
    parts = rel.lower().split("/")
    inside = "/".join(parts[2:])
    return inside if parts[0] == "workspaces" and inside else None


def _private_reason(rel, ignored, shown=None):
    """Why the file at `rel` may not be versioned at all, or None; the path is written as `shown` (by
    default `rel`). Case does not matter: on Windows (the prod disk) Data/ is data/."""
    shown = shown or rel
    lower = rel.lower()
    if lower.startswith(tuple(p.lower() for p in BLOCK_PREFIXES)):
        return f"PATH-BLOCK: {shown} is under a private surface"
    inside = _inside_workspace(rel) or ""
    name = lower.rsplit("/", 1)[-1]
    if (inside.startswith(WORKSPACE_PRIVATE_DIRS) or inside in WORKSPACE_PRIVATE_FILES
            or (inside and name.endswith(WORKSPACE_ARCHIVE_SUFFIXES))
            or (name.startswith(".env") and name != ".env.example")
            or Path(name).suffix in PRIVATE_SUFFIXES or rel in ignored):
        return f"PRIVATE: {shown} is a {PRIVATE_FILE}"
    return None


class GitError(Exception):
    """git could not run or failed: the guard fails closed, since an empty answer would pass."""


def _git(*args, stdin=None, ok=(0,)):
    """git's output; GitError when git is missing or exits with a code not in `ok`. Paths come out as
    they are (core.quotePath=false), not as octal escapes that would hide a value written in them."""
    try:
        done = subprocess.run(["git", "-c", "core.quotePath=false", *args], input=stdin, capture_output=True, encoding="utf-8",
                              errors="replace", check=False)
    except OSError as e:
        raise GitError(f"git {args[0]} could not run ({type(e).__name__}: {e})") from e
    if done.returncode not in ok:
        said = done.stderr.strip().partition("\n")[0]   # its first line says what went wrong
        raise GitError(f"git {args[0]} failed with exit code {done.returncode}" + (f": {said}" if said else ""))
    return done.stdout


def _outside(path, top):
    """Whether `path` is outside the repository at `top`. A path relative to the cwd (inside the
    repository) without '..' is inside: what pre-commit and --all pass. Otherwise its directory is
    resolved (a Windows short name, as git prints the long one) but not the file itself: a versioned
    symlink (a data/ linked to another disk) is inside, and must still be asked about."""
    if not Path(path).is_absolute() and ".." not in Path(path).parts:
        return False
    full = Path(os.path.abspath(path))
    return not (Path(os.path.realpath(full.parent)) / full.name).is_relative_to(Path(os.path.realpath(top)))


def _gitignored(files):
    """The files .gitignore excludes: tracked or staged anyway, they were force-added. check-ignore
    exits 1 when none is excluded. A file outside the repository (the commit message file, which a
    worktree keeps in the main checkout's .git/) is never versioned, so git is not asked about it."""
    if not files:
        return set()
    top = Path(_git("rev-parse", "--show-toplevel").strip())
    files = [f for f in files if not _outside(f, top)]
    if not files:
        return set()
    out = _git("check-ignore", "--no-index", "-z", "--stdin", stdin="\0".join(files) + "\0", ok=(0, 1))
    return {p for p in out.split("\0") if p}


def _read_bytes(path):
    """The file's bytes; None for a missing or unreadable file."""
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _decode(data):
    """Bytes as text in UTF-16 (with BOM), UTF-8 or cp1252, with CRLF read as LF (a value split across
    lines measures the same on a Windows checkout). Every file is read, a binary one or a text with a
    stray NUL included: digits written in it are found all the same."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n")


def _binary_in_workspace(rel, data):
    inside = _inside_workspace(rel)
    return inside is not None and b"\0" in data and Path(inside).suffix not in PUBLIC_BINARY_SUFFIXES


def check_files(files, sources):
    """Every finding in these files, in order; a private file is reported without being read."""
    norms = [Path(f).as_posix() for f in files]
    ignored = _gitignored(norms)
    failures = []
    for rel in norms:
        shown, failures_in_path = _path_failures(rel, sources)
        failures += failures_in_path
        reason = _private_reason(rel, ignored, shown)
        if reason:
            failures.append(reason)
            continue
        data = _read_bytes(Path(rel))
        if data is None:
            continue
        if _binary_in_workspace(rel, data):
            failures.append(f"PRIVATE: {shown} is a {BINARY_IN_WORKSPACE}")
            continue
        failures += check_text(rel, _decode(data), sources, where=lambda n, shown=shown: f"{shown}:{n}")
    return failures


def check_history(sources):
    """Every version of every file, every commit message, merges included, and every path ever committed.
    The paths are read NUL-separated: without -z git quotes a non-ASCII one ("relat\\303\\263rio.db") and
    its suffix would not match. The paths' own findings are in the log, in its diff headers (written
    unquoted, see _git)."""
    log = _git("log", "--all", "-p", "-m", "--no-color", "--format=commit %H%n%B")
    failures = check_text("history", log, sources)
    paths = {p for p in _git("log", "--all", "-m", "--name-only", "-z", "--format=").split("\0") if p}
    return failures + [f"history: {_path_failures(p, sources)[0]}: private file was committed"
                       for p in sorted(paths) if _private_reason(p, set())]
