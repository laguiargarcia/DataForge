"""The leak guard's real values: where they come from (GETFINANCE_DATA, .leakguard-workspaces, the main
checkout's workspaces/GetFinance/data/), how they are read (raw parquet, edicao.db, .env) and how they
are found in a text. Part of scripts/leakguard.py (df-n8ac.36), whose docstring describes the whole
guard. Only the stdlib at import: polars is imported where raw parquet is read."""
import ast
import json
import os
import re
import sqlite3
from pathlib import Path

from scripts import leakguard_checkout, leakguard_patterns

# The real data's workspaces: one directory per line, gitignored like .leakguard-local.
LISTED_WORKSPACES_FILE = ".leakguard-workspaces"
DATA_VARIABLE = "GETFINANCE_DATA"   # a workspace's data/ directory, set on the command
# Declared, never assumed: whoever has no real data at all (another contributor) sets it to 1.
NO_DATA_VARIABLE = "LEAKGUARD_NO_REAL_DATA"
REAL_WORKSPACE = "GetFinance"   # the workspace whose data/ is real, read from the main checkout unnamed
SYNTHETIC_MARKER = "SYNTHETIC"   # in data/raw/: written by the demo's generate_synthetic_data.py
EDICAO_DB = "edicao.db"   # in data/: the edits database, real categories and rules
# In the attribution files (NAME_EXEMPT_PATHS), a real value read from an owner column is let through.
ATTRIBUTION_SOURCES = (".owner",)

MIN_VALUE_LENGTH = 6   # shorter real values ("CDB", "Pix") are words that code and docs use
MIN_WORD_LENGTH = 12   # a single plain word ("NETFLIX") is only a value to block when this long
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.+Z-]*)?")   # a date or timestamp identifies no one
NUMBER_LIKE = re.compile(r"[\d\s./-]+")   # digits and separators only: an account, a document, an id
# Between the digits of a real number: any run (or none) of whitespace (a line break too), '.', '_',
# '/', '-', en and em dash, or a single comma ("12345,6"). Wide on purpose: this list is the owner's
# real values, so a false alarm is cheaper than a leak. A comma followed by a space is NOT a
# separator: that is a list of numbers ("3, 1, 4, 1, 5, 9" is not the number 314159; the patterns'
# own weights table was one). What still escapes, on purpose: that comma-and-space form, and a letter
# or other symbol as separator ("12345 x 6", "12345|6", "12345:6").
SEPARATOR_CHARS = r"\s.\-_/\u2013\u2014"
GAP = leakguard_patterns._RUN_OR_COMMA.format(SEPARATOR_CHARS)
DIGIT_SEPARATOR = GAP + "?"
# Raw columns whose string leaves identify the owner, an account or a counterparty. Amounts, dates
# and descriptions are left out: they are not identifiers and would match ordinary text.
RAW_COLUMNS = {
    "accounts": ("id", "itemId", "number", "owner", "taxNumber", "bankData", "creditData"),
    "transactions": ("id", "accountId", "paymentData", "merchant"),
    "investments": ("id", "number", "itemId", "owner", "code"),
    "bills": ("id", "accountId"),
}


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
    return bool(NUMBER_LIKE.fullmatch(value)) and len(leakguard_patterns._digits(value)) >= MIN_VALUE_LENGTH


def _worth_blocking(leaf):
    """Long enough, and not a short plain word that ordinary code or prose would also contain."""
    if len(leaf) < MIN_VALUE_LENGTH or ISO_DATE.fullmatch(leaf) or len(set(leaf)) == 1:
        return False   # short, a date, or a filler like "00000000" (also a public bank ISPB)
    if _is_number_like(leaf):
        return len(set(leakguard_patterns._digits(leaf))) > 1
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
        return re.compile(r"(?<!\d)" + DIGIT_SEPARATOR.join(leakguard_patterns._digits(value)) + r"(?!\d)")
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
            if leakguard_patterns._digits(value) not in merged:
                continue
        elif re.sub(r"\s+", " ", value) not in spaced:
            continue
        hits += [(leakguard_patterns._line(text, m.start()), value, source) for m in _value_pattern(value).finditer(text)]
    return sorted(hits)


def _real_value_failures(rel, text, sources, where):
    """REAL findings; in the attribution files a value read from an owner column is let through."""
    exempt = set()
    if rel in leakguard_patterns.NAME_EXEMPT_PATHS:
        exempt = {v for _, values in sources for v, src in values.items() if src.endswith(ATTRIBUTION_SOURCES)}
    return [f"REAL: {where(n)} -> {leakguard_patterns._mask(v, f", {src} from {ws}")}"
            for ws, values in sources
            for n, v, src in real_value_hits(text, {k: s for k, s in values.items() if k not in exempt})]


def _listed_workspaces():
    """[(directory, how it is named)] for each workspace in .leakguard-workspaces ('#' comments; '~'
    expanded; a relative path is taken from the main checkout's root)."""
    listing = leakguard_checkout.MAIN_ROOT / LISTED_WORKSPACES_FILE
    if not listing.is_file():
        return []
    entries = (line.strip() for line in listing.read_text(encoding="utf-8", errors="ignore").splitlines())
    return [(leakguard_checkout.MAIN_ROOT / Path(entry).expanduser(), f"{entry} (listed in {LISTED_WORKSPACES_FILE})")
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
    ws = leakguard_checkout.MAIN_ROOT / "workspaces" / REAL_WORKSPACE
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
    failures = [f"REAL-VALUES: {leakguard_checkout.MAIN_ROOT_ERROR}"] if leakguard_checkout.MAIN_ROOT_ERROR else []
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
