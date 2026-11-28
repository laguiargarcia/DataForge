"""What a PR changes in the drawing: the base's arquitetura/modelo.json against the PR's, in Markdown (df-8x0m.23.38).

    git show origin/main:arquitetura/modelo.json | python3 scripts/arquitetura/diff.py

The base model comes on stdin; an empty stdin is a base without modelo.json, so everything is new.
The PR's model is arquitetura/modelo.json (modelo.SAIDA); without it everything is removed. Prints
nothing when the two draw the same. The command line takes no arguments, so no value reaches a git
call or a file path: the CI job `report` runs git itself (.github/workflows/ci.yml) and
scripts/ci_report.py posts the output as its own PR comment.

Each list of records (elements, arrows, views, deployment nodes, instances and arrows) is matched by
id: new, removed, or changed with the fields that differ. An arrow's id holds its title (modelo.py),
so a renamed arrow is a removed and a new one with the same source and target: those are paired
(in id order) and shown as one changed arrow.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arquitetura import modelo

# (heading, path in the model) of each list of records, in the order the comment shows them.
SECTIONS = (
    ("Elements", ("elements",)),
    ("Arrows", ("relations",)),
    ("Views", ("views",)),
    ("Deployment nodes", ("deployment", "nodes")),
    ("Deployment instances", ("deployment", "instances")),
    ("Deployment arrows", ("deployment", "relations")),
)
ARROWS = {("relations",), ("deployment", "relations")}
SPEC_KEYS = ("elements", "tags", "relationships", "deployments")
HEADING = "### 🗺️ Drawing changes (`arquitetura/modelo.json`)"
SHORT = 80


def _records(model: dict | None, path: tuple[str, ...]) -> dict[str, dict]:
    node = model or {}
    for key in path[:-1]:
        node = node.get(key) or {}
    return {r["id"]: r for r in node.get(path[-1]) or []}


def _short(value) -> str:
    """A value inline: in backticks, on one line, cut at SHORT characters; None is a dash."""
    if value is None:
        return "—"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.replace("`", "'").split())
    return f"`{text[:SHORT - 1] + '…' if len(text) > SHORT else text}`"


def _field_change(field: str, old, new) -> str:
    if isinstance(old, list) and isinstance(new, list) and all(isinstance(v, str) for v in old + new):
        gone, came = [v for v in old if v not in new], [v for v in new if v not in old]
        return f"{field} " + " ".join([f"+{_short(v)}" for v in came] + [f"−{_short(v)}" for v in gone])
    return f"{field} {_short(old)} → {_short(new)}"


def changes(old: dict, new: dict, skip: frozenset[str] = frozenset()) -> list[str]:
    """One text per field that differs, in field order; id and the fields in `skip` aside."""
    return [_field_change(f, old.get(f), new.get(f)) for f in sorted((set(old) | set(new)) - {"id"} - skip)
            if old.get(f) != new.get(f)]


def _label(record: dict) -> str:
    kind = record.get("kind") or record.get("type") or record.get("element")
    title = record.get("title")
    shown_title = title and title not in record["id"]
    return f"`{record['id']}`" + (f" ({kind})" if kind else "") + (f" {_short(title)}" if shown_title else "")


def _pair_renamed(gone: list[dict], came: list[dict]) -> list[tuple[dict, dict]]:
    """Removed and new arrows with the same source and target, paired in id order."""
    by_ends = defaultdict(lambda: ([], []))
    for side, records in ((0, gone), (1, came)):
        for r in records:
            by_ends[(r["source"], r["target"])][side].append(r)
    return [pair for removed, added in by_ends.values() for pair in zip(removed, added, strict=False)]


def section(heading: str, path: tuple[str, ...], base: dict | None, pr: dict | None) -> list[str]:
    """The Markdown lines of one list of records; [] when it did not change."""
    old, new = _records(base, path), _records(pr, path)
    gone = [old[i] for i in sorted(old.keys() - new.keys())]
    came = [new[i] for i in sorted(new.keys() - old.keys())]
    changed = [(old[i], new[i]) for i in sorted(old.keys() & new.keys()) if old[i] != new[i]]
    if path in ARROWS:
        renamed = _pair_renamed(gone, came)
        gone = [r for r in gone if all(r is not o for o, _ in renamed)]
        came = [r for r in came if all(r is not n for _, n in renamed)]
        changed = sorted(changed + renamed, key=lambda pair: pair[1]["id"])
    if not (gone or came or changed):
        return []
    counts = [f"{n} {what}" for n, what in ((len(came), "new"), (len(gone), "removed"), (len(changed), "changed")) if n]
    lines = [f"**{heading}**: {' · '.join(counts)}"]
    lines += [f"- ➕ {_label(r)}" for r in came]
    lines += [f"- ➖ {_label(r)}" for r in gone]
    for o, n in changed:
        renamed = o["id"] != n["id"]  # a renamed arrow: its id already shows the new title
        details = changes(o, n, frozenset({"title"}) if renamed else frozenset())
        lines.append(f"- ✏️ `{n['id']}`" + (f" (was `{o['id']}`)" if renamed else "")
                     + (f": {'; '.join(details)}" if details else ""))
    return [*lines, ""]


def _spec_lines(base: dict | None, pr: dict | None) -> list[str]:
    old, new = (base or {}).get("specification") or {}, (pr or {}).get("specification") or {}
    texts = [_field_change(key, old.get(key) or [], new.get(key) or []) for key in SPEC_KEYS
             if (old.get(key) or []) != (new.get(key) or [])]
    return [f"**Specification**: {'; '.join(texts)}", ""] if texts else []


def markdown(base: dict | None, pr: dict | None) -> str:
    """The comment's Markdown: what `pr` changes against `base` (None: no model); "" when nothing."""
    lines = [line for heading, path in SECTIONS for line in section(heading, path, base, pr)]
    lines += _spec_lines(base, pr)
    if (base or {}).get("likec4") != (pr or {}).get("likec4") and base and pr:
        lines += [f"**LikeC4**: {_short(base.get('likec4'))} → {_short(pr.get('likec4'))}", ""]
    if not lines:
        return ""
    note = "" if base else "The base has no `arquitetura/modelo.json`: everything is new.\n\n"
    return f"{HEADING}\n\n{note}" + "\n".join(lines).rstrip() + "\n"


def main() -> int:
    text = sys.stdin.read()
    base = json.loads(text) if text.strip() else None
    pr = modelo.load() if modelo.SAIDA.exists() else None
    sys.stdout.write(markdown(base, pr))
    return 0


if __name__ == "__main__":
    sys.exit(main())
