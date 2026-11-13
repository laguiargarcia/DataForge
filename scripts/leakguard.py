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
GetFinance-Workspace checkout) lists the real values in its data (account numbers, names, ids) via
real_values(); any committed file holding one fails, masked to its first two characters. A pattern
cannot recognize a real account number in a docstring example; that list can. No private workspace
in CI, so CI runs without it; a private guard that is present but cannot run fails the check.

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


def _main_checkout_workspaces(here):
    """workspaces/ of the main checkout of the repo at `here`: private workspaces are gitignored,
    so a worktree has none."""
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=here, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return here / "workspaces"
    return Path(common).parent / "workspaces"


WORKSPACES = _main_checkout_workspaces(Path(__file__).resolve().parent.parent)
# In the attribution files the owner's name is intended (see NAME_EXEMPT_PATHS); only values whose
# source is an owner column are let through there, never an account number or an id.
ATTRIBUTION_SOURCES = (".owner",)

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
#   2. a gitignored '.leakguard-local' file at the repo root (one regex per line; '#' comments).
# On a fresh public clone both are absent and the NAME check is simply skipped — the
# generic CPF/ACCT/EMAIL rules and the private-path block still apply. The owner keeps
# their name patterns in the gitignored '.leakguard-local' for full local protection.
def _load_owner_name_patterns():
    raw = []
    env = os.environ.get("LEAKGUARD_NAME_PATTERNS", "")
    raw += [s for s in env.split(os.pathsep) if s.strip()]
    local = Path(__file__).resolve().parent.parent / ".leakguard-local"
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


def load_real_values():
    """({lowercased value: (source, workspace)}, allowed values, failures) from every private guard."""
    values, allowed, failures = {}, set(), []
    for guard in sorted(WORKSPACES.glob("*/tools/leakguard.py")):
        ws = guard.parent.parent
        rel = guard.relative_to(WORKSPACES.parent).as_posix()
        try:
            spec = importlib.util.spec_from_file_location(f"private_leakguard_{ws.name}", guard)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for value, source in module.real_values(ws).items():
                values.setdefault(value.lower(), (source, ws.name))
            allowed |= {a.lower() for a in module._allowed()}
        except Exception as e:  # noqa: BLE001 -- any failure of a private guard must fail closed, not pass
            failures.append(f"REAL-VALUES: {rel} could not run ({type(e).__name__}: {e}); nothing was checked against it")
    return {v: src for v, src in values.items() if v not in allowed}, failures


def _real_value_failures(norm, text, real):
    lowered = text.lower()
    present = [(v, src) for v, src in real.items() if v in lowered
               and not (norm in NAME_EXEMPT_PATHS and src[0].endswith(ATTRIBUTION_SOURCES))]
    return [f"REAL: {norm}:{n} -> '{v[:2]}…' ({len(v)} chars, {src} from {ws})"
            for n, line in enumerate(lowered.splitlines(), start=1)
            for v, (src, ws) in present if v in line]


def _file_failures(f, real=None):
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
            if not _is_exempt(m.group(0))] + _real_value_failures(norm, text, real or {})


def main(argv):
    files = argv[1:]
    real, failures = load_real_values()
    failures += [msg for f in files for msg in _file_failures(f, real)]

    if failures:
        print("LEAK GUARD FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1

    print(f"leak guard: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
