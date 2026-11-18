#!/usr/bin/env python3
"""Leak guard: fails when a file about to be versioned is private, or holds personal data or a real value.

The one leak guard of this repo (df-bsb2.2.2: it took in the GetFinance workspace's own guard).
pre-commit passes it the staged files, CI runs it with --all (every tracked file), and --history
audits every version of every file, every commit message and every path ever committed.

1. Private files. Surfaces that are never published (PATH-BLOCK: docs/, .beads/, ...). Inside a
   workspace (workspaces/<name>/): data/, report/, docs/archive/, docs/workbook.md and the config
   files that hold real values. Anywhere: a .env other than .env.example, a data artifact (.db,
   .parquet, .xlsx...), and any file that .gitignore excludes (tracked anyway, it was force-added).
2. Patterns: a CPF or CNPJ whose check digits are valid (an invalid one is a made-up test value),
   with or without punctuation or spaces; a bank agency/account short code (NNNN-N; not a date part,
   not one repeated digit); a personal e-mail address; the owner's name. The name patterns are never
   written here: they come from LEAKGUARD_NAME_PATTERNS and the gitignored .leakguard-local.
3. Real values, on the owner's machine: identifiers, names and documents read from a workspace's
   data/raw (Pluggy raw), data/edicao.db (category rules) and .env. A pattern cannot tell a real
   account number in a docstring example from a fake one (df-k37o); this list can. A number matches
   with any or no separator between its digits, a name with any whitespace between its words. The
   data comes from GETFINANCE_DATA (a workspace's data/ directory) and from each workspace listed in
   the gitignored .leakguard-workspaces at the main checkout's root, also from a worktree. Without
   either (a fresh clone, CI) there is no real-value check; a source that names no data, or yields
   no value, fails the check instead of letting it pass unchecked.

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
PRIVATE_FILE = "private file (data, report, .env, real config, a data artifact or gitignored)"

# ---------------------------------------------------------------------------- patterns

# A document's digits may be split by punctuation or spaces; a trailing sentence period is not a digit,
# and digits inside a word (a hash in uv.lock) are not a document.
_NOT_BEFORE = r"(?<!\w)(?<!\d[.,])"
_NOT_AFTER = r"(?!\w)(?![.,]\d)"
CPF = re.compile(_NOT_BEFORE + r"\d{3}[. ]?\d{3}[. ]?\d{3}[- ]?\d{2}" + _NOT_AFTER)
CNPJ = re.compile(_NOT_BEFORE + r"\d{2}[. ]?\d{3}[. ]?\d{3}[/ ]?\d{4}[- ]?\d{2}" + _NOT_AFTER)
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
DIGIT_SEPARATOR = r"[\s./-]?"
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
    if (workspace / "data" / "edicao.db").is_file():
        sources.append(_edicao_values(workspace / "data" / "edicao.db"))
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
    merged = re.sub(r"(?<=\d)[\s./-]+(?=\d)", "", text)
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


def load_real_values():
    """([(workspace name, its real values)], failures) for GETFINANCE_DATA and the listed workspaces."""
    failures = [f"REAL-VALUES: {MAIN_ROOT_ERROR}"] if MAIN_ROOT_ERROR else []
    named, named_failures = _named_workspace()
    failures += named_failures
    sources, seen = [], set()
    for ws, how in named + _listed_workspaces():
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
    checks = [("CPF", CPF, lambda s: _cpf_valid(_digits(s))),
              ("CNPJ", CNPJ, lambda s: _cnpj_valid(_digits(s))),
              ("ACCT", ACCT, lambda s: len(set(_digits(s))) > 1),   # "0000-0" is a filler
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


def _private_reason(rel, ignored):
    """Why the file at `rel` may not be versioned at all, or None."""
    if rel.startswith(BLOCK_PREFIXES):
        return f"PATH-BLOCK: {rel} is under a private surface"
    parts = rel.split("/")
    inside = "/".join(parts[2:]) if parts[0] == "workspaces" else ""
    name = parts[-1]
    if (inside.startswith(WORKSPACE_PRIVATE_DIRS) or inside in WORKSPACE_PRIVATE_FILES
            or (name.startswith(".env") and name != ".env.example")
            or Path(name).suffix.lower() in PRIVATE_SUFFIXES or rel in ignored):
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


def _read_text(path):
    """A text file's content in UTF-8, UTF-16 (with BOM) or cp1252; None for a binary or unreadable file."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data:
        return None   # binary (fonts, images): gitleaks covers secrets there
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


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
        text = _read_text(Path(rel)) if Path(rel).is_file() else None
        if text is not None:
            failures += check_text(rel, text, sources)
    return failures


def check_history(sources):
    """Every version of every file, every commit message, merges included, and every path ever committed."""
    log = _git("log", "--all", "-p", "-m", "--no-color", "--format=commit %H%n%B")
    failures = check_text("history", log, sources)
    paths = {p for p in _git("log", "--all", "-m", "--name-only", "--format=").splitlines() if p}
    return failures + [f"history: {p}: private file was committed" for p in sorted(paths) if _private_reason(p, set())]


def _scope(sources):
    if not sources:
        return f"no real values: neither {DATA_VARIABLE} nor {LISTED_WORKSPACES_FILE} names any"
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
