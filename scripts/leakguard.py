#!/usr/bin/env python3
"""Leak guard: fails the commit if staged files contain PII or live under a
private path. Invoked by pre-commit (passes staged filenames as argv) and by CI
(scans a path set). Defense-in-depth on top of .gitignore (git add -f bypasses it).

Path-block prefixes: workspaces/GetFinance/, workspaces/GetFinance-Dev/, .planning/,
.beads/, tests/, docs/. The public Show workspace (workspaces/GetFinance-Show/) is
NOT blocked — it is the committed surface.

PII patterns: Brazilian CPF (11-digit), bank agency/account short codes, owner name
variants, personal email addresses.

ALLOWLIST: synthetic document literals emitted by
workspaces/GetFinance-Show/scripts/generate_synthetic_data.py.  These are fabricated
values (the CPF is checksum-INVALID; the CNPJs are fictional) used to populate
dim_counterparty in the Show workspace.  They must not trigger the guard.
"""
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Private-path prefixes — any staged file under these surfaces is hard-blocked.
# workspaces/GetFinance-Show/ is intentionally absent (public output).
# ---------------------------------------------------------------------------
BLOCK_PREFIXES = (
    "workspaces/GetFinance/",
    "workspaces/GetFinance-Dev/",
    ".planning/",
    ".beads/",
    "tests/",      # D9: tests never committed to public repo
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
ACCT  = re.compile(r"\b\d{4,5}-\d{1}\b")
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


def main(argv):
    files = argv[1:]
    failures = []

    for f in files:
        p = Path(f)
        norm = p.as_posix()

        # --- path-block check -------------------------------------------
        if any(norm.startswith(pre) for pre in BLOCK_PREFIXES):
            failures.append(f"PATH-BLOCK: {norm} is under a private surface")
            continue

        if not p.is_file():
            continue

        if p.suffix in BINARY_SUFFIXES:
            continue

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # --- content checks ----------------------------------------------
        checks = [("CPF", CPF), ("ACCT", ACCT), ("EMAIL", EMAILS)]
        if norm not in NAME_EXEMPT_PATHS:
            checks += [("NAME", rx) for rx in NAME_PATTERNS]
        for label, rx in checks:
            for m in rx.finditer(text):
                hit = m.group(0)
                # Strip punctuation for allowlist lookup (CPF/ACCT may have dots/dashes)
                bare = re.sub(r"[\s.\-]", "", hit)
                if bare in ALLOWLIST or hit in ALLOWLIST:
                    continue  # synthetic literal — exempt
                failures.append(f"{label}: {norm} -> {hit!r}")

    if failures:
        print("LEAK GUARD FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1

    print(f"leak guard: {len(files)} file(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
