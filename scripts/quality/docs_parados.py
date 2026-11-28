"""Stale docs (df-8x0m.23.39.3): a warning, never a failure, when the branch changed a file that a
doc's front matter `code:` covers and did not change that doc. Also counts the sketch docs
(`status: esboço`). See docs/quality-gate.md, "Stale docs".

Changed = the files that differ from the merge-base with origin/main, committed or not, plus the
untracked ones. A file covered by several docs warns only the most specific: the doc whose matching
code: path is the longest (df-ev4u), so a container doc with `code: [dataforge]` stays quiet when a
component doc covers the file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: arquitetura.docs

from arquitetura import docs

BASE = "origin/main"
DOCS = "docs"


def _git(root: Path, *args: str) -> list[str] | None:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, encoding="utf-8", errors="replace", check=False)
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else None


def mudados(root: Path, base: str = BASE) -> set[str] | None:
    """The changed files of the branch (module docstring); None when there is no merge-base."""
    mb = _git(root, "merge-base", base, "HEAD")
    if not mb:
        return None
    diff = _git(root, "diff", "-z", "--no-renames", "--name-only", mb[0].strip()) or []
    novos = _git(root, "ls-files", "-z", "--others", "--exclude-standard") or []
    return set(diff) | set(novos)


def ler_docs(root: Path) -> dict[str, dict]:
    """{doc path: front matter} of every docs/**/*.md with a front matter that reads."""
    out = {}
    for f in sorted((root / DOCS).rglob("*.md")):
        try:
            fm = docs.ler_front_matter(f)
        except (ValueError, UnicodeDecodeError, OSError):
            continue  # a broken front matter is docs.py conferir's to report
        if fm:
            out[f.relative_to(root).as_posix()] = fm
    return out


def _alcance(code: list, path: str) -> int:
    """The length of the longest code: path covering `path`; -1 when none does."""
    best = -1
    for c in code:
        if isinstance(c, str) and c.strip() and (path == c or path.startswith(c.rstrip("/") + "/")):
            best = max(best, len(c.rstrip("/")))
    return best


def parados(changed: set[str], fms: dict[str, dict]) -> dict[str, list[str]]:
    """{doc: [changed files it covers]} for the docs that did not change, each file given only to its
    most specific docs."""
    out: dict[str, list[str]] = {}
    for path in sorted(changed - set(fms)):
        reach = {d: _alcance(fm.get("code") or [], path) for d, fm in fms.items() if isinstance(fm.get("code"), list)}
        best = max(reach.values(), default=-1)
        if best < 0:
            continue
        for d in sorted(d for d, n in reach.items() if n == best and d not in changed):
            out.setdefault(d, []).append(path)
    return out


def relatorio(root: Path, base: str | None = None) -> list[str]:
    """The lines the gate prints: the sketch count and one warning per stale doc."""
    base = base or BASE
    fms = ler_docs(root)
    lines = [f"docs: {sum(fm.get('status') == 'esboço' for fm in fms.values())} of {len(fms)} in sketch (status esboço)"]
    changed = mudados(root, base)
    if changed is None:
        return [*lines, f"docs: no merge-base with {base}; stale-doc check skipped"]
    for d, files in parados(changed, fms).items():
        lines.append(f"warning: {d} was not changed, but the code it covers was: {', '.join(files)}")
    return lines
