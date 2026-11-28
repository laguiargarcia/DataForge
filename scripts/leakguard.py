#!/usr/bin/env python3
"""Leak guard: fails when a file about to be versioned is private, or holds personal data or a real value.

The one leak guard of this repo (df-bsb2.2.2: it took in the GetFinance workspace's own guard).
pre-commit passes it the staged files, CI runs it with --all (every tracked file), and --history
audits every version of every file, every commit message and every path ever committed.

1. Private files, whatever the case of their path. Surfaces that are never published (PATH-BLOCK:
   .beads/, the history under docs/ (superpowers/, phases/...), ...; the rest of docs/ is versioned
   and read like any file, df-azm6). Inside a workspace (workspaces/<name>/): data/, report/, docs/archive/,
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
   data/raw (Pluggy raw), data/edicao.db (category rules) and .env; and from raw, the names and shops
   in transaction descriptions, amounts with cents and a card's last four digits (df-88xr). A pattern
   cannot tell a real account number in a docstring example from a fake one (df-k37o); this list
   can. A number matches with any or no separator between its digits, a name with any whitespace
   between its words, an amount written as money, four card digits only next to what names a card
   (leakguard_values says how each kind is found and what it lets through on purpose). The
   data comes from GETFINANCE_DATA (a workspace's data/ directory), from each workspace listed in
   the gitignored .leakguard-workspaces at the main checkout's root, and from the main checkout's
   workspaces/GetFinance/data/ when it holds raw/ or edicao.db and is not the demo's synthetic data
   (raw/SYNTHETIC); all of these also from a worktree.
   A source that names no data, or yields no value, fails the check. With no source at all the
   check fails too, and says what to set, except in CI (no real data there by design) and where
   LEAKGUARD_NO_REAL_DATA=1 declares that this machine has no real data: an absent variable or
   listing never passes for "no real data".

Findings never echo a value in full: they show its first two characters and its length (CI logs
are public). A path is a text like any other: its name is checked too, and a path with a finding in
it is shown masked the same way. When git fails (not a repository, no git), the check fails.

The guard is split by part (df-n8ac.36), each module its own mutation target: this file is the
command; leakguard_files the private files, the file and path checks and git (1); leakguard_patterns
the patterns (2); leakguard_values the real values (3); leakguard_checkout the main checkout.
"""
import argparse
import sys
from pathlib import Path

if __name__ == "__main__":
    # Run as a command: the parts are reached as the scripts package, the one name tests import them by.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import leakguard_files, leakguard_values


def _scope(sources):
    if not sources:
        return f"no real values: {leakguard_values._no_data_waiver() or 'none found'}"
    names = ", ".join(ws for ws, _ in sources)
    return f"{sum(len(values) for _, values in sources)} real values from {names}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", help="files to check (pre-commit passes the staged ones)")
    parser.add_argument("--all", action="store_true", help="check every tracked file")
    parser.add_argument("--history", action="store_true", help="check every commit, message and path in git history")
    args = parser.parse_args(argv)

    sources, failures = leakguard_values.load_real_values()
    try:
        if args.history:
            failures += leakguard_files.check_history(sources)
            what = "history"
        else:
            files = [p for p in leakguard_files._git("ls-files", "-z").split("\0") if p] if args.all else args.files
            failures += leakguard_files.check_files(files, sources)
            what = f"{len(files)} file(s)"
    except leakguard_files.GitError as e:   # git's own words may name a path: shown like one
        failures.append(f"GIT: {leakguard_files._path_failures(str(e), sources)[0]}; nothing was checked")
    if failures:
        print(f"LEAK GUARD FAILED ({_scope(sources)}):\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    print(f"leak guard: {what} clean ({_scope(sources)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
