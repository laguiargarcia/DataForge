#!/usr/bin/env python3
"""Leak guard: fails the commit if staged files contain PII or live under a
private path. Invoked by pre-commit (passes staged filenames as argv) and by CI
(scans a path set). Defense-in-depth on top of .gitignore (git add -f bypasses it).

Path-block prefixes: workspaces/GetFinance/, workspaces/GetFinance-Dev/, .planning/,
.beads/, tests/workspaces/, tests/test_finance_daily_dag.py, docs/.
Engine tests under tests/ use fake data and are public. The public Show workspace (workspaces/GetFinance-Show/) is
NOT blocked — it is the committed surface.

PII patterns: Brazilian CPF (11-digit), bank agency/account short codes, owner name
variants, personal email addresses.

Real values (df-k37o): a private workspace on this machine that ships tools/leakguard.py (e.g. the
GetFinance-Workspace checkout) lists the real values in its data (account numbers, names, ids). Any
committed file holding one fails, masked to its first two characters. A pattern cannot recognize a
real account number in a docstring example; that list can. The private guard's public API is used,
so the matching (separators between digits, names wrapped across lines) lives in one place:
real_values(workspace), allowed(), real_value_hits(text, values). No private workspace in CI, so
CI runs without it. Fails closed: a private guard that cannot run, or a worktree whose main checkout
(where the gitignored private workspaces and .leakguard-local live) cannot be found, fails the check.

ALLOWLIST: synthetic document literals emitted by
workspaces/GetFinance-Show/scripts/generate_synthetic_data.py.  These are fabricated
values (the CPF is checksum-INVALID; the CNPJs are fictional) used to populate
dim_counterparty in the Show workspace.  They must not trigger the guard.
"""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path


def _main_checkout(here):
    """(root of the main checkout of the repo at `here`, error). Gitignored files (the private
    workspaces, .leakguard-local) exist only there, never in a worktree. The error is set when `here`
    is a worktree whose main checkout cannot be found: checking without them would pass silently."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=here, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        common = ""
    if common and Path(common).is_dir():   # an old git echoes the unknown flag instead of failing
        return Path(common).parent, None
    if (here / ".git").is_file():
        return here, ("this is a worktree and its main checkout could not be found, so the private "
                      "workspaces and .leakguard-local were not checked")
    return here, None


MAIN_ROOT, MAIN_ROOT_ERROR = _main_checkout(Path(__file__).resolve().parent.parent)
WORKSPACES = MAIN_ROOT / "workspaces"
# In the attribution files the owner's name is intended (see NAME_EXEMPT_PATHS); only a value that
# some private workspace sources from an owner column is let through there, never an account or an id.
ATTRIBUTION_SOURCES = (".owner",)
MIN_REAL_VALUE_LENGTH = 6   # a shorter value would match everywhere, and its 2-character mask would show it

# ---------------------------------------------------------------------------
# Private-path prefixes — any staged file under these surfaces is hard-blocked.
# workspaces/GetFinance-Show/ is intentionally absent (public output).
# ---------------------------------------------------------------------------
BLOCK_PREFIXES = (
    "workspaces/GetFinance/",
    "workspaces/GetFinance-Dev/",
    ".planning/",
    ".beads/",
    "tests/workspaces/",                 # pipeline tests read real workspaces
    "tests/test_finance_daily_dag.py",   # same
    "tests/test_testloop_evals.py",      # tests of the private eval harness
    "docs/",       # D9: docs never committed to public repo
)

# ---------------------------------------------------------------------------
# Synthetic literals from generate_synthetic_data.py — ALLOWLIST.
# These are FAKE values: "11122233344" is a checksum-invalid CPF,
# "12345678000190" and "98765432000155" are fictional CNPJs.
# Exempt any regex hit whose matched text equals one of these strings exactly.
# ---------------------------------------------------------------------------
ALLOWLIST = {"11122233344", "12345678000190", "98765432000155"}

# Files where the owner's name is INTENTIONAL attribution (MIT copyright, README author),
# not a leak. The NAME check is skipped for these paths; CPF/ACCT/EMAIL still apply.
NAME_EXEMPT_PATHS = {"LICENSE", "README.md"}

# ---------------------------------------------------------------------------
# PII regexes
# CPF: 11-digit Brazilian tax ID, with or without punctuation (NNN.NNN.NNN-NN or NNNNNNNNNNN)
# ACCT: 4-5 digit prefix dash 1 digit (agency/account short codes).
#       Tuned against the committed set (S5, Task 13) — synthetic ids use
#       acc-bank-0001 style to avoid bare digit runs; years/versions/ports
#       must not false-positive.
# NAMES / EMAILS: owner identifiers blocked from the public surface.
# ---------------------------------------------------------------------------
CPF   = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
ACCT  = re.compile(r"\b\d{4,5}-\d\b")
EMAILS = re.compile(r"(?i)\b[\w.+-]+@(gmail|outlook)\.com\b")

# Owner-specific NAME patterns are deliberately NOT hardcoded in this public guard
# (that would put the owner's name into the committed source AND make the guard /
# leak-audit flag themselves). They are loaded at runtime from, in order:
#   1. the LEAKGUARD_NAME_PATTERNS env var (os.pathsep-separated regexes), and
#   2. a gitignored '.leakguard-local' file at the main checkout's root (one regex per line; '#'
#      comments), also when the commit is made from a worktree, which has none of its own.
# On a fresh public clone both are absent and the NAME check is simply skipped — the
# generic CPF/ACCT/EMAIL rules and the private-path block still apply. The owner keeps
# their name patterns in the gitignored '.leakguard-local' for full local protection.
def _load_owner_name_patterns(root=None):
    raw = []
    env = os.environ.get("LEAKGUARD_NAME_PATTERNS", "")
    raw += [s for s in env.split(os.pathsep) if s.strip()]
    local = (root or MAIN_ROOT) / ".leakguard-local"
    if local.is_file():
        for line in local.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                raw.append(line)
    return [re.compile(p, re.IGNORECASE) for p in raw]

NAME_PATTERNS = _load_owner_name_patterns()

# Binary artifacts are content-scanned by gitleaks / xlsx-audit, not here.
BINARY_SUFFIXES = {".xlsx", ".parquet", ".db", ".duckdb"}


def _content_checks(norm):
    """(label, regex) pairs for one file: CPF/ACCT/EMAIL always, owner names unless exempt."""
    checks = [("CPF", CPF), ("ACCT", ACCT), ("EMAIL", EMAILS)]
    if norm not in NAME_EXEMPT_PATHS:
        checks += [("NAME", rx) for rx in NAME_PATTERNS]
    return checks


def _readable_text(p):
    """The text to scan, or None for a missing file, a binary artifact or an unreadable file."""
    if not p.is_file() or p.suffix in BINARY_SUFFIXES:
        return None
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def _is_exempt(hit):
    # Strip punctuation for allowlist lookup (CPF/ACCT may have dots/dashes)
    bare = re.sub(r"[\s.\-]", "", hit)
    return bare in ALLOWLIST or hit in ALLOWLIST   # synthetic literal — exempt


def _private_guard(ws):
    """(module, its usable real values) for one private workspace; each workspace's allow-list filters
    only its own values, and only strings long enough to match safely are kept."""
    guard = ws / "tools" / "leakguard.py"
    spec = importlib.util.spec_from_file_location(f"private_leakguard_{ws.name}", guard)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    allowed = {a.lower() for a in module.allowed()}
    values = {v.lower(): src for v, src in module.real_values(ws).items()
              if isinstance(v, str) and len(v.strip()) >= MIN_REAL_VALUE_LENGTH and v.lower() not in allowed}
    return module, values


def load_real_values():
    """([(workspace name, its guard module, its real values)], failures) for every private guard."""
    guards, failures = [], []
    if MAIN_ROOT_ERROR:
        failures.append(f"REAL-VALUES: {MAIN_ROOT_ERROR}")
    for guard in sorted(WORKSPACES.glob("*/tools/leakguard.py")):
        ws = guard.parent.parent
        try:
            module, values = _private_guard(ws)
        except Exception as e:  # noqa: BLE001 -- any failure of a private guard must fail closed rather than pass
            rel = guard.relative_to(WORKSPACES.parent).as_posix()
            failures.append(f"REAL-VALUES: {rel} could not run ({type(e).__name__}: {e}); nothing was checked against it")
            continue
        guards.append((ws.name, module, values))
    return guards, failures


def _owner_values(guards):
    """Values that any private workspace sources from an owner column: attribution in LICENSE/README."""
    return {v for _, _, values in guards for v, src in values.items() if src.endswith(ATTRIBUTION_SOURCES)}


def _real_value_failures(norm, text, guards):
    exempt = _owner_values(guards) if norm in NAME_EXEMPT_PATHS else set()
    failures = []
    for ws, module, values in guards:
        for n, v, src in module.real_value_hits(text, {k: s for k, s in values.items() if k not in exempt}):
            failures.append(f"REAL: {norm}:{n} -> '{v[:2]}…' ({len(v)} chars, {src} from {ws})")
    return failures


def _file_failures(f, guards=()):
    p = Path(f)
    norm = p.as_posix()
    # --- path-block check -------------------------------------------
    if any(norm.startswith(pre) for pre in BLOCK_PREFIXES):
        return [f"PATH-BLOCK: {norm} is under a private surface"]
    text = _readable_text(p)
    if text is None:
        return []
    # --- content checks ----------------------------------------------
    return [f"{label}: {norm} -> {m.group(0)!r}"
            for label, rx in _content_checks(norm)
            for m in rx.finditer(text)
            if not _is_exempt(m.group(0))] + _real_value_failures(norm, text, guards)


def main(argv):
    files = argv[1:]
    guards, failures = load_real_values()
    failures += [msg for f in files for msg in _file_failures(f, guards)]

    if failures:
        print("LEAK GUARD FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1

    print(f"leak guard: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
