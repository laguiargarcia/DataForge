"""The leak guard's files: which paths are private, how a file and a path are read and checked, and
what git says (the files .gitignore excludes, every tracked file, the whole history). Part of
scripts/leakguard.py (df-n8ac.36), whose docstring describes the whole guard."""
import collections
import itertools
import os
import re
import subprocess
import uuid
from pathlib import Path

from scripts import leakguard_patterns, leakguard_values

# Surfaces of this repo that are never published.
BLOCK_PREFIXES = (
    "workspaces/GetFinance-Dev/",
    ".planning/",
    ".beads/",
    "tests/workspaces/",                 # pipeline tests that read real workspaces
    "tests/test_finance_daily_dag.py",   # same
    "tests/test_testloop_evals.py",      # tests of the private eval harness
    # docs/ is versioned (df-azm6) but its history holds real values, and so does the owner's
    # workbook note: the same paths as in .gitignore.
    "docs/superpowers/",
    "docs/phases/",
    "docs/gsd-workflow.md",
    "docs/workbook-handoff.md",
)
# Inside a workspace (workspaces/<name>/...): where its real data and its real config live.
WORKSPACE_PRIVATE_DIRS = ("data/", "report/", "docs/archive/")
WORKSPACE_PRIVATE_FILES = {"docs/workbook.md", "config/investments.yaml"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".duckdb", ".parquet", ".xlsx", ".xlsm", ".xls"}
# Inside a workspace, an archive is a copy of its data (backup_dados.py zips data/raw and edicao.db),
# whatever the name; it is judged by its path, since its content cannot be read.
WORKSPACE_ARCHIVE_SUFFIXES = (".zip", ".7z", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".tar", ".rar")
# Inside a workspace, any other binary file is refused too (a dump under any name), except a built
# app's web fonts and images: public assets that hold no record.
PUBLIC_BINARY_SUFFIXES = {".woff", ".woff2", ".ttf", ".otf", ".eot", ".png", ".jpg", ".jpeg", ".gif",
                          ".ico", ".webp"}
PRIVATE_FILE = "private file (data, report, .env, real config, a data artifact or gitignored)"
HISTORY = "history"   # what the audit of the history calls its text: no file name, so no file's exemption
UTF16_MARKS = (b"\xff\xfe", b"\xfe\xff")   # the byte order marks: little and big endian
BINARY_IN_WORKSPACE = "binary file inside a workspace (a dump or a backup cannot be read for real values)"


def check_text(rel, text, sources, where=None):
    """Every finding in `text`, located by `where(line)` (by default `rel:line`)."""
    where = where or (lambda n: f"{rel}:{n}")
    return (leakguard_patterns._pattern_failures(rel, text, where)
            + leakguard_values._real_value_failures(rel, text, sources, where))


def _path_failures(rel, sources):
    """(the path as findings may show it, the findings in the path itself). A path with a finding in it
    is shown masked: CI logs are public, and a file named after a document would publish it."""
    failures = check_text(rel, rel, sources, where=lambda _: f"the path {leakguard_patterns._mask(rel)}")
    return (leakguard_patterns._mask(rel) if failures else rel), failures


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


def _git(*args, stdin=None, ok=(0,), text=True):
    """git's output, as UTF-8 text or, with text=False, as the bytes it wrote (the content of a file
    that is not UTF-8); GitError when git is missing or exits with a code not in `ok`. Paths come out as
    they are (core.quotePath=false), not as octal escapes that would hide a value written in them."""
    how = {"encoding": "utf-8", "errors": "replace"} if text else {}
    try:
        done = subprocess.run(["git", "-c", "core.quotePath=false", *args], input=stdin, capture_output=True, check=False, **how)
    except OSError as e:
        raise GitError(f"git {args[0]} could not run ({type(e).__name__}: {e})") from e
    if done.returncode not in ok:
        wrote = done.stderr if text else done.stderr.decode(errors="replace")
        said = wrote.strip().partition("\n")[0]   # its first line says what went wrong
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
    if data.startswith(UTF16_MARKS):
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


def _utf16_blobs():
    """(path, bytes) of every file version in history that starts with a UTF-16 byte order mark, each
    once, under the first path it was committed at. The log cannot show them: its text is read as
    UTF-8, where a UTF-16 letter is a letter and a NUL."""
    path_of = {}
    for line in _git("rev-list", "--all", "--objects").splitlines():
        sha, _, path = line.partition(" ")
        if path:
            path_of.setdefault(sha, path)
    if not path_of:
        return []
    out = _git("cat-file", "--batch", stdin="".join(f"{sha}\n" for sha in path_of).encode(), text=False)
    blobs, at = [], 0
    while at < len(out):
        end = out.index(b"\n", at)
        sha, kind, size = out[at:end].decode().split()
        data = out[end + 1:end + 1 + int(size)]
        at = end + 1 + int(size) + 1   # git ends each object with a line feed
        if kind == "blob" and data.startswith(UTF16_MARKS):
            blobs.append((path_of[sha], data))
    return blobs


# A commit whose message holds a finding the owner decided to leave in the history: full hash -> the bead
# of the decision. Only the message is let through; the commit's diff is still read.
ACCEPTED_MESSAGES = {"d2cb9cf0544c4667918927a500d6170456599830": "df-0eat"}
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)   # only these ranges, not the context after
MERGE_LOGIN = re.compile(r"^Merge pull request #\d+ from [^/\s]+/")   # the subject only; the login is public on GitHub
FIRST_HUNK = re.compile(r"^@@ ", re.MULTILINE)


def _blank(match):
    """The matched text as spaces of the same length, so no line and no column moves."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _log_format(nonce):
    """The log's frame: markers at the start of a line, named by a nonce chosen for this run. A line of
    a diff starts with ' ', '+', '-' or a backslash, so no file's content can forge one; a message
    could, only by guessing the nonce."""
    return f"--format={nonce}C %H%n{nonce}M%n%B%n{nonce}E"


def _commit_parts(log, nonce):
    """(hash, message, diff) of each commit in the log (a merge shows once per parent under -m)."""
    frame = rf"^{nonce}C (\S+)\n{nonce}M\n(.*?)\n{nonce}E\n"
    return itertools.batched(re.split(frame, log, flags=re.MULTILINE | re.DOTALL)[1:], 3, strict=True)


def _section_path(header):
    """The path a section's file has, from its '+++ b/' line ('--- a/' when the file is gone); None when
    it names none (a rename with no change, a mode change): nothing then earns a file's exemption."""
    after, before = None, None
    for line in header.split("\n"):
        if line.startswith("+++ b/"):
            after = line[6:].rstrip("\t")
        elif line.startswith("--- a/"):
            before = line[6:].rstrip("\t")
    return after or before


def _section_name(header):
    """The file a section with no '+++'/'---' line is about (a rename with no change, a mode change),
    to say where a finding is: its 'rename to' line, else the path its 'diff --git' line names on both
    sides; None for text before the first file. Never an exemption: only _section_path gives one."""
    lines = header.split("\n")
    renamed = [line.removeprefix("rename to ") for line in lines if line.startswith("rename to ")]
    if renamed:
        return renamed[0]
    both = lines[0].removeprefix("diff --git a/")
    half = (len(both) - 3) // 2
    return both[:half] if both != lines[0] and both[half:half + 3] == " b/" and both[half + 3:] == both[:half] else None


def _log_text(data):
    """The log's bytes as text, CRLF read as LF and a lone CR as a space. Text mode would read a lone CR
    (a binary shown with --text, a file of CR-only lines) as a new line, which content could use to
    start a forged 'diff --git' section that earns a file's exemption."""
    return data.decode(errors="replace").replace("\r\n", "\n").replace("\r", " ")


def _keep_counts(found, failures):
    """Add `found` (the findings of one text) to `failures` (a Counter): each finding as many times as
    one text holds it, at most. Two values masked alike on one line are two; the same finding in the
    diff of a merge against each parent (-m) is one."""
    for failure, count in collections.Counter(found).items():
        failures[failure] = max(failures[failure], count)


def _file_sections(diff):
    """(path or None, its header text, its body) for each file of a commit's diff, the text before the
    first file included (nothing may be left unread). A section starts at its 'diff --git' line; the
    body starts at the first hunk."""
    starts = [m.start() for m in re.finditer(r"^diff --git ", diff, re.MULTILINE)]
    for start, end in itertools.pairwise([0, *starts, len(diff)]):
        section = diff[start:end]
        hunk = FIRST_HUNK.search(section)
        cut = hunk.start() if hunk else len(section)
        yield _section_path(section[:cut]), section[:cut], section[cut:]


def _history_failures(log, sources, nonce):
    """The findings in the log, each commit read as its message and, apart, each file of its diff: the
    header with no exemption (a rename into LICENSE shows its old name there), the hunks under their
    file's own, as in check_files. Each finding once: -m shows a merge's diff per parent."""
    failures, shown_of = collections.Counter(), {}
    for sha, message, diff in _commit_parts(log, nonce):
        short = f"history {sha[:7]}"
        if sha not in ACCEPTED_MESSAGES:
            text = MERGE_LOGIN.sub(_blank, message)
            _keep_counts(check_text(HISTORY, text, sources, where=lambda n, short=short: f"{short} msg:{n}"), failures)
        for path, header, body in _file_sections(diff):
            name = path or _section_name(header)
            if name not in shown_of:
                shown_of[name] = _path_failures(name, sources)[0] if name else "diff"
            shown, lines = shown_of[name], header.count("\n")
            _keep_counts(check_text(HISTORY, header, sources, where=lambda n, short=short, shown=shown: f"{short} {shown}:{n}"),
                         failures)
            _keep_counts(check_text(path or HISTORY, HUNK_HEADER.sub(_blank, body), sources,
                                    where=lambda n, short=short, shown=shown, lines=lines: f"{short} {shown}:{n + lines}"),
                         failures)
    return list(failures.elements())


def check_history(sources):
    """Every version of every file, every commit message, merges included, and every path ever committed.
    The paths are read NUL-separated: without -z git quotes a non-ASCII one ("relat\\303\\263rio.db") and
    its suffix would not match. The paths' own findings are in the log, in its diff headers (written
    unquoted, see _git). --text makes the log show what git takes for binary (a text with a NUL, where it
    would print 'Binary files differ'); a UTF-16 version is read apart, decoded as a file is. Noise that
    is no leak is let through: a hunk header's line ranges, the login of a merge's message, and what
    check_files lets through in its path (LICENSE, uv.lock)."""
    nonce = uuid.uuid4().hex
    log = _log_text(_git("log", "--all", "-p", "-m", "--text", "--no-color", _log_format(nonce), text=False))
    failures = _history_failures(log, sources, nonce)
    for path, data in _utf16_blobs():
        shown = _path_failures(path, sources)[0]
        failures += check_text(HISTORY, _decode(data), sources, where=lambda n, shown=shown: f"history: {shown}:{n}")
    paths = {p for p in _git("log", "--all", "-m", "--name-only", "-z", "--format=").split("\0") if p}
    return failures + [f"history: {_path_failures(p, sources)[0]}: private file was committed"
                       for p in sorted(paths) if _private_reason(p, set())]
