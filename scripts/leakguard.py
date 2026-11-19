#!/usr/bin/env python3
"""Leak guard: fails when a file about to be versioned is private, or holds personal data or a real value.

The one leak guard of this repo (df-bsb2.2.2: it took in the GetFinance workspace's own guard).
pre-commit passes it the staged files, CI runs it with --all (every tracked file), and --history
audits every version of every file, every commit message and every path ever committed.

1. Private files, whatever the case of their path. Surfaces that are never published (PATH-BLOCK:
   docs/, .beads/, ...). Inside a workspace (workspaces/<name>/): data/, report/, docs/archive/,
   docs/workbook.md, the config files that hold real values, an archive (.zip, .7z, .gz...) and any
   binary file other than a web font or an image. Anywhere: a .env other than .env.example, a data
   artifact (.db, .parquet, .xlsx...), and any file that .gitignore excludes (tracked anyway, it was
   force-added).
2. Patterns: a CPF or CNPJ whose check digits are valid (an invalid one is a made-up test value),
   with or without punctuation, spaces or tabs, also next to a letter (not in uv.lock's hashes); a
   bank agency/account short code (NNNN-N; not a date part, not one repeated digit); a personal
   e-mail address; the owner's name. The name patterns are never written here: they come from
   LEAKGUARD_NAME_PATTERNS and the gitignored .leakguard-local. Every file is read, one with a NUL
   byte too.
3. Real values, on the owner's machine: identifiers, names and documents read from a workspace's
   data/raw (Pluggy raw), data/edicao.db (category rules) and .env. A pattern cannot tell a real
   account number in a docstring example from a fake one (df-k37o); this list can. A number matches
   with any or no separator between its digits, a name with any whitespace between its words. The
   data comes from GETFINANCE_DATA (a workspace's data/ directory), from each workspace listed in
   the gitignored .leakguard-workspaces at the main checkout's root, and from the main checkout's
   workspaces/GetFinance/data/ when it holds raw/ or edicao.db and is not the demo's synthetic data
   (raw/SYNTHETIC); all of these also from a worktree.
   A source that names no data, or yields no value, fails the check. With no source at all the
   check fails too, and says what to set, except in CI (no real data there by design) and where
   LEAKGUARD_NO_REAL_DATA=1 declares that this machine has no real data: an absent variable or
   listing never passes for "no real data".

Findings never echo a value in full: they show its first two characters and its length (CI logs
are public).
"""
import argparse
import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
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
# The real data's workspaces: one directory per line, gitignored like .leakguard-local.
LISTED_WORKSPACES_FILE = ".leakguard-workspaces"
DATA_VARIABLE = "GETFINANCE_DATA"   # a workspace's data/ directory, set on the command
# Declared, never assumed: whoever has no real data at all (another contributor) sets it to 1.
NO_DATA_VARIABLE = "LEAKGUARD_NO_REAL_DATA"
REAL_WORKSPACE = "GetFinance"   # the workspace whose data/ is real, read from the main checkout unnamed
SYNTHETIC_MARKER = "SYNTHETIC"   # in data/raw/: written by the demo's generate_synthetic_data.py
EDICAO_DB = "edicao.db"   # in data/: the edits database, real categories and rules

# ---------------------------------------------------------------------------- private files

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

# ---------------------------------------------------------------------------- patterns

# A document's digits may be split by punctuation, spaces or tabs (not by a line break); a trailing
# sentence period is not a digit, and digits inside a longer number are not a document. A letter or
# '_' next to it does not hide one ("cpf_" then the digits).
_NOT_BEFORE = r"(?<!\d)(?<!\d[.,])"
_NOT_AFTER = r"(?!\d)(?![.,]\d)"
# Between the groups any run (or none) of the characters people put between digits: space, tab, '.',
# '_', '/', '-', en and em dash, or a single comma ("123.456.789 - 00", "123_456_789_00"). Never a line break: a
# document wrapped across lines is two things, not one (the real-value check below does join those).
# A wrong guess costs little: only a match whose check digits are valid is a finding.

# One gap between digits: a run of the given characters, or a single comma (a comma and a space is
# a list of numbers, not a gap). Module-level expressions, not helper functions: mutmut cannot test
# what only runs at import.
_RUN_OR_COMMA = r"(?:[{}]+|,)"
_DOC_GAP = _RUN_OR_COMMA.format(r"\t .\-_/\u2013\u2014") + "?"
_D2, _D3, _D4 = r"\d{2}", r"\d{3}", r"\d{4}"
CPF = re.compile(_NOT_BEFORE + _DOC_GAP.join([_D3, _D3, _D3, _D2]) + _NOT_AFTER)
CNPJ = re.compile(_NOT_BEFORE + _DOC_GAP.join([_D2, _D3, _D3, _D4, _D2]) + _NOT_AFTER)
# Files of hex digests and package names only, where a run of digits inside a hash may pass a
# check-digit rule: the document patterns do not apply there (every other check does).
DIGEST_FILES = {"uv.lock"}
# Agency/account short codes. "2026-4-10" is a date, not an account.
ACCT = re.compile(r"\b\d{4,5}-\d\b(?!-\d)")
EMAIL = re.compile(
    r"(?i)\b[\w.+-]+@(?:gmail|googlemail|outlook|hotmail|live|msn|yahoo|icloud|me|uol|bol|terra|ig"
    r"|globo|globomail|r7|zipmail|proton|protonmail|pm|tutanota|gmx|aol|yandex|mail)"
    r"\.(?:com|me|net|org|ch|de)(?:\.br)?\b")
CPF_LENGTH = 11
CNPJ_LENGTH = 14
CNPJ_WEIGHTS = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

# Files where the owner's name is INTENTIONAL attribution (MIT copyright, README author), not a
# leak: the NAME check is skipped there, and a real value read from an owner column is let through.
NAME_EXEMPT_PATHS = {"LICENSE", "README.md"}
ATTRIBUTION_SOURCES = (".owner",)


def _load_owner_name_patterns(root=None):
    """The owner's name patterns, never hardcoded here (that would publish the name): from the
    LEAKGUARD_NAME_PATTERNS variable (os.pathsep-separated regexes) and the gitignored
    '.leakguard-local' at the main checkout's root (one regex per line; '#' comments). On a fresh
    public clone both are absent and the NAME check is skipped; every other check still applies."""
    raw = [s for s in os.environ.get("LEAKGUARD_NAME_PATTERNS", "").split(os.pathsep) if s.strip()]
    local = (root or MAIN_ROOT) / ".leakguard-local"
    if local.is_file():
        lines = (line.strip() for line in local.read_text(encoding="utf-8", errors="ignore").splitlines())
        raw += [line for line in lines if line and not line.startswith("#")]
    return [re.compile(p, re.IGNORECASE) for p in raw]


NAME_PATTERNS = _load_owner_name_patterns()

# ---------------------------------------------------------------------------- real values

MIN_VALUE_LENGTH = 6   # shorter real values ("CDB", "Pix") are words that code and docs use
MIN_WORD_LENGTH = 12   # a single plain word ("NETFLIX") is only a value to block when this long
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.+Z-]*)?")   # a date or timestamp identifies no one
NUMBER_LIKE = re.compile(r"[\d\s./-]+")   # digits and separators only: an account, a document, an id
# Between the digits of a real number: any run (or none) of whitespace (a line break too), '.', '_',
# '/', '-', en and em dash, or a single comma ("12345,6"). Wide on purpose: this list is the owner's
# real values, so a false alarm is cheaper than a leak. A comma followed by a space is NOT a
# separator: that is a list of numbers ("3, 1, 4, 1, 5, 9" is not the number 314159; this file's own
# weights table was one). What still escapes, on purpose: that comma-and-space form, and a letter or
# other symbol as separator ("12345 x 6", "12345|6", "12345:6").
SEPARATOR_CHARS = r"\s.\-_/\u2013\u2014"
GAP = _RUN_OR_COMMA.format(SEPARATOR_CHARS)
DIGIT_SEPARATOR = GAP + "?"
# Raw columns whose string leaves identify the owner, an account or a counterparty. Amounts, dates
# and descriptions are left out: they are not identifiers and would match ordinary text.
RAW_COLUMNS = {
    "accounts": ("id", "itemId", "number", "owner", "taxNumber", "bankData", "creditData"),
    "transactions": ("id", "accountId", "paymentData", "merchant"),
    "investments": ("id", "number", "itemId", "owner", "code"),
    "bills": ("id", "accountId"),
}


def _digits(value):
    return re.sub(r"\D", "", value)


def _check_digits_hold(digits, weights):
    """Each check digit is the mod-11 rule over the digits before it, with the weights that end there."""
    for size in (len(digits) - 2, len(digits) - 1):
        total = sum(int(d) * w for d, w in zip(digits[:size], weights[len(weights) - size:], strict=True))
        if (0 if total % 11 < 2 else 11 - total % 11) != int(digits[size]):   # noqa: PLR2004 -- the rule itself
            return False
    return True


def _cpf_valid(digits):
    return len(digits) == CPF_LENGTH and len(set(digits)) > 1 and _check_digits_hold(digits, range(11, 1, -1))


def _cnpj_valid(digits):
    return len(digits) == CNPJ_LENGTH and len(set(digits)) > 1 and _check_digits_hold(digits, CNPJ_WEIGHTS)


def _parse_nested(text):
    """The object a JSON or Python-literal text holds, or None when it is plain text."""
    if text[:1] not in "{[":
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(text)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
    return None


def _string_leaves(value):
    """Every string inside a value, descending into JSON or Python-literal text, dicts and lists.

    Raw keeps Pluggy's nested objects as text, sometimes JSON and sometimes a Python repr (single
    quotes, None): both are opened, so e.g. bankData.transferNumber is a value of its own."""
    if isinstance(value, str):
        nested = _parse_nested(value.strip())
        if nested is None:
            yield value.strip()
        else:
            yield from _string_leaves(nested)
    elif isinstance(value, (dict, list)):
        for item in (value.values() if isinstance(value, dict) else value):
            yield from _string_leaves(item)
    elif value is not None and not isinstance(value, (bool, float)):
        yield str(value)


def _is_number_like(value):
    return bool(NUMBER_LIKE.fullmatch(value)) and len(_digits(value)) >= MIN_VALUE_LENGTH


def _worth_blocking(leaf):
    """Long enough, and not a short plain word that ordinary code or prose would also contain."""
    if len(leaf) < MIN_VALUE_LENGTH or ISO_DATE.fullmatch(leaf) or len(set(leaf)) == 1:
        return False   # short, a date, or a filler like "00000000" (also a public bank ISPB)
    if _is_number_like(leaf):
        return len(set(_digits(leaf))) > 1
    return not leaf.isalpha() or len(leaf) >= MIN_WORD_LENGTH


def _expand(leaf):
    """The leaf, plus each '/'-part of a number like a transferNumber ('bank/agency/account'), since
    an account segment may be written alone."""
    yield leaf
    if "/" in leaf and _is_number_like(leaf):
        yield from (part.strip() for part in leaf.split("/"))


def _raw_values(raw):
    import polars as pl   # noqa: PLC0415 -- only where real data exists; CI and a fresh clone never need polars
    for entity, columns in RAW_COLUMNS.items():
        for part in sorted((raw / entity).glob("*/data.parquet")):
            present = [c for c in columns if c in pl.read_parquet_schema(part)]
            frame = pl.read_parquet(part, columns=present)
            for col in present:
                for value in frame[col].to_list():
                    yield value, f"raw.{entity}.{col}"


def _edicao_values(edicao):
    conn = sqlite3.connect(f"file:{edicao.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT padrao FROM mapeamento").fetchall()
    finally:
        conn.close()
    for (padrao,) in rows:
        yield padrao, "edicao.mapeamento"


def _strip_inline_comment(value):
    """`abc # note` -> `abc `: a '#' after whitespace starts a comment (a '#' inside a value stays)."""
    for i in range(1, len(value)):
        if value[i] == "#" and value[i - 1].isspace():
            return value[:i]
    return value


def _env_values(env):
    """Every value in .env (any key): `export KEY=value`, quotes and inline comments are handled."""
    for line in env.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.removeprefix("export ").partition("=")
        value = _strip_inline_comment(value.strip()).strip().strip("'\"")
        for item in value.split(","):
            yield item.strip(), f".env.{key.strip()}"


def real_values(workspace):
    """{lowercased real value: where it came from} for one workspace; empty when it has no data."""
    sources = []
    if (workspace / "data" / "raw").is_dir():
        sources.append(_raw_values(workspace / "data" / "raw"))
    if (workspace / "data" / EDICAO_DB).is_file():
        sources.append(_edicao_values(workspace / "data" / EDICAO_DB))
    if (workspace / ".env").is_file():
        sources.append(_env_values(workspace / ".env"))
    found = {}
    for source in sources:
        for value, origin in source:
            for leaf in (x for raw_leaf in _string_leaves(value) for x in _expand(raw_leaf)):
                if _worth_blocking(leaf):
                    found.setdefault(leaf.lower(), origin)
    return found


def _value_pattern(value):
    if _is_number_like(value):
        return re.compile(r"(?<!\d)" + DIGIT_SEPARATOR.join(_digits(value)) + r"(?!\d)")
    return re.compile(r"\s+".join(re.escape(word) for word in value.split()), re.IGNORECASE)


def real_value_hits(text, values):
    """(line number, value, source) for each real value in `text`.

    Case-insensitive; a name matches with any whitespace between its words (wrapped across lines), a
    number with any or no separator between its digits. A cheap substring test on normalized text
    runs first, so the regex only runs for the few values that may be there."""
    spaced = re.sub(r"\s+", " ", text.lower())
    merged = re.sub(rf"(?<=\d){GAP}(?=\d)", "", text)
    hits = []
    for value, source in values.items():
        if _is_number_like(value):
            if _digits(value) not in merged:
                continue
        elif re.sub(r"\s+", " ", value) not in spaced:
            continue
        hits += [(_line(text, m.start()), value, source) for m in _value_pattern(value).finditer(text)]
    return sorted(hits)


def _listed_workspaces():
    """[(directory, how it is named)] for each workspace in .leakguard-workspaces ('#' comments; '~'
    expanded; a relative path is taken from the main checkout's root)."""
    listing = MAIN_ROOT / LISTED_WORKSPACES_FILE
    if not listing.is_file():
        return []
    entries = (line.strip() for line in listing.read_text(encoding="utf-8", errors="ignore").splitlines())
    return [(MAIN_ROOT / Path(entry).expanduser(), f"{entry} (listed in {LISTED_WORKSPACES_FILE})")
            for entry in entries if entry and not entry.startswith("#")]


def _named_workspace():
    """([(the workspace above GETFINANCE_DATA, how it is named)], failures); nothing when it is unset."""
    named = os.environ.get(DATA_VARIABLE)
    if not named:
        return [], []
    data = Path(named)
    if data.name != "data" or not (data / "raw").is_dir():
        return [], [f"REAL-VALUES: {DATA_VARIABLE}={named} is not a workspace's data/ directory with raw/ in it"]
    return [(data.parent, f"{DATA_VARIABLE}={named}")], []


def _found_workspaces():
    """[(workspace, how it is named)] for the main checkout's workspaces/GetFinance/ when its data/
    holds raw/ or edicao.db: the real data where the pipeline writes it, read with nothing named.
    Not when raw/ holds the demo's SYNTHETIC marker (its generator writes only into a raw/ of its own):
    made-up values read as real would flag the generator itself. Other workspaces are synthetic."""
    ws = MAIN_ROOT / "workspaces" / REAL_WORKSPACE
    data = ws / "data"
    if (data / "raw" / SYNTHETIC_MARKER).is_file():
        return []
    if (data / "raw").is_dir() or (data / EDICAO_DB).is_file():
        return [(ws, f"{ws} (found in the main checkout)")]
    return []


def _no_data_waiver():
    """Why a run with no source of real values may pass, or None: CI has no real data by design, and
    whoever has none at all declares it with LEAKGUARD_NO_REAL_DATA=1."""
    if os.environ.get("CI"):
        return "CI"
    if os.environ.get(NO_DATA_VARIABLE) == "1":
        return f"{NO_DATA_VARIABLE}=1"
    return None


NO_SOURCE = (f"REAL-VALUES: no real values to check against: neither {DATA_VARIABLE}, {LISTED_WORKSPACES_FILE} "
             f"nor a workspaces/{REAL_WORKSPACE}/data/ of the main checkout names any. Outside CI the guard needs "
             "the real "
             f"data: set {DATA_VARIABLE}=<workspace>/data on the command ({DATA_VARIABLE}=<workspace>/data git "
             f"commit ...); with no real data at all, declare it with {NO_DATA_VARIABLE}=1")


def load_real_values():
    """([(workspace name, its real values)], failures) for GETFINANCE_DATA, the listed workspaces and
    the data/ directories of the main checkout's workspaces. With none of them, outside CI and without
    the waiver, the run fails: an absent variable or listing must not pass as "no real data"."""
    failures = [f"REAL-VALUES: {MAIN_ROOT_ERROR}"] if MAIN_ROOT_ERROR else []
    named, named_failures = _named_workspace()
    failures += named_failures
    candidates = named + _listed_workspaces() + _found_workspaces()
    if not candidates and not named_failures and _no_data_waiver() is None:
        failures.append(NO_SOURCE)
    sources, seen = [], set()
    for ws, how in candidates:
        if ws.resolve() in seen:
            continue
        seen.add(ws.resolve())
        try:
            values = real_values(ws)
        except Exception as e:  # noqa: BLE001 -- any failure to read the real data must fail closed rather than pass
            failures.append(f"REAL-VALUES: {how}: its data could not be read ({type(e).__name__}: {e})")
            continue
        if not values:
            failures.append(f"REAL-VALUES: {how} yields no real value (no data/raw, data/edicao.db or .env "
                            "there); nothing was checked against it")
            continue
        sources.append((ws.name, values))
    return sources, failures


# ---------------------------------------------------------------------------- checks


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def _mask(value, about=""):
    return f"'{value[:2]}…' ({len(value)} chars{about})"


def _pattern_checks(rel):
    """(label, regex, test the match must pass to be a finding or None) for one file."""
    checks = [] if rel in DIGEST_FILES else [("CPF", CPF, lambda s: _cpf_valid(_digits(s))),
                                             ("CNPJ", CNPJ, lambda s: _cnpj_valid(_digits(s)))]
    checks += [("ACCT", ACCT, lambda s: len(set(_digits(s))) > 1),   # "0000-0" is a filler
               ("EMAIL", EMAIL, None)]
    if rel not in NAME_EXEMPT_PATHS:
        checks += [("NAME", rx, None) for rx in NAME_PATTERNS]
    return checks


def _pattern_failures(rel, text):
    return [f"{label}: {rel}:{_line(text, m.start())} -> {_mask(m.group(0))}"
            for label, rx, keep in _pattern_checks(rel)
            for m in rx.finditer(text)
            if keep is None or keep(m.group(0))]


def _real_value_failures(rel, text, sources):
    """REAL findings; in the attribution files a value read from an owner column is let through."""
    exempt = set()
    if rel in NAME_EXEMPT_PATHS:
        exempt = {v for _, values in sources for v, src in values.items() if src.endswith(ATTRIBUTION_SOURCES)}
    return [f"REAL: {rel}:{n} -> {_mask(v, f", {src} from {ws}")}"
            for ws, values in sources
            for n, v, src in real_value_hits(text, {k: s for k, s in values.items() if k not in exempt})]


def check_text(rel, text, sources):
    return _pattern_failures(rel, text) + _real_value_failures(rel, text, sources)


def _inside_workspace(rel):
    """The path of `rel` inside its workspace (workspaces/<name>/...), lowercased; None outside one."""
    parts = rel.lower().split("/")
    inside = "/".join(parts[2:])
    return inside if parts[0] == "workspaces" and inside else None


def _private_reason(rel, ignored):
    """Why the file at `rel` may not be versioned at all, or None. Case does not matter: on Windows
    (the prod disk) Data/ is data/."""
    lower = rel.lower()
    if lower.startswith(tuple(p.lower() for p in BLOCK_PREFIXES)):
        return f"PATH-BLOCK: {rel} is under a private surface"
    inside = _inside_workspace(rel) or ""
    name = lower.rsplit("/", 1)[-1]
    if (inside.startswith(WORKSPACE_PRIVATE_DIRS) or inside in WORKSPACE_PRIVATE_FILES
            or (inside and name.endswith(WORKSPACE_ARCHIVE_SUFFIXES))
            or (name.startswith(".env") and name != ".env.example")
            or Path(name).suffix in PRIVATE_SUFFIXES or rel in ignored):
        return f"PRIVATE: {rel} is a {PRIVATE_FILE}"
    return None


def _git(*args, stdin=None):
    return subprocess.run(["git", *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False).stdout


def _gitignored(files):
    """The files .gitignore excludes: tracked or staged anyway, they were force-added."""
    if not files:
        return set()
    out = _git("check-ignore", "--no-index", "-z", "--stdin", stdin="\0".join(files) + "\0")
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
        reason = _private_reason(rel, ignored)
        if reason:
            failures.append(reason)
            continue
        data = _read_bytes(Path(rel))
        if data is None:
            continue
        if _binary_in_workspace(rel, data):
            failures.append(f"PRIVATE: {rel} is a {BINARY_IN_WORKSPACE}")
            continue
        failures += check_text(rel, _decode(data), sources)
    return failures


def check_history(sources):
    """Every version of every file, every commit message, merges included, and every path ever committed."""
    log = _git("log", "--all", "-p", "-m", "--no-color", "--format=commit %H%n%B")
    failures = check_text("history", log, sources)
    paths = {p for p in _git("log", "--all", "-m", "--name-only", "--format=").splitlines() if p}
    return failures + [f"history: {p}: private file was committed" for p in sorted(paths) if _private_reason(p, set())]


def _scope(sources):
    if not sources:
        return f"no real values: {_no_data_waiver() or 'none found'}"
    names = ", ".join(ws for ws, _ in sources)
    return f"{sum(len(values) for _, values in sources)} real values from {names}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", help="files to check (pre-commit passes the staged ones)")
    parser.add_argument("--all", action="store_true", help="check every tracked file")
    parser.add_argument("--history", action="store_true", help="check every commit, message and path in git history")
    args = parser.parse_args(argv)

    sources, failures = load_real_values()
    if args.history:
        failures += check_history(sources)
        what = "history"
    else:
        files = [p for p in _git("ls-files", "-z").split("\0") if p] if args.all else args.files
        failures += check_files(files, sources)
        what = f"{len(files)} file(s)"
    if failures:
        print(f"LEAK GUARD FAILED ({_scope(sources)}):\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    print(f"leak guard: {what} clean ({_scope(sources)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
