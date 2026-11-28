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
(in id order) and shown as one changed arrow. A model without modelo.json's shape (not JSON, a
list that is not of records with an id, an id twice) stops it with a message and exit 1.
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


def _one_line(value) -> str:
    """A value as text on one line, without backticks; anything but a string as its JSON."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return " ".join(text.replace("`", "'").split())


def _window(text: str, start: int) -> str:
    """SHORT characters of `text` from `start`, in backticks, with … where it was cut."""
    piece = text[start:start + SHORT]
    return f"`{'…' if start else ''}{piece}{'…' if start + SHORT < len(text) else ''}`"


class _Absent:
    """A key one side of a dict does not have: not the same as the key set to null."""

    def __repr__(self) -> str:
        return "absent"


ABSENT = _Absent()


def _short(value) -> str:
    """A value inline: in backticks, on one line, cut at SHORT characters; None is a dash."""
    if value is ABSENT:
        return "(absent)"
    return "—" if value is None else _window(_one_line(value), 0)


def _pair(old, new) -> tuple[str, str]:
    """Two values inline, both cut around their first difference: a change past SHORT characters still shows."""
    if old is None or new is None or ABSENT in (old, new):
        return _short(old), _short(new)
    a, b = _one_line(old), _one_line(new)
    first = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    start = max(0, first - SHORT // 4) if max(len(a), len(b)) > SHORT else 0
    return _window(a, start), _window(b, start)


def _differing(old: dict, new: dict, skip: frozenset[str] = frozenset()) -> list[tuple[str, object, object]]:
    """(key, old value, new value) of each key that differs, sorted: a key on one side only differs
    even when the other side has it null."""
    return [(key, old.get(key, ABSENT), new.get(key, ABSENT)) for key in sorted((set(old) | set(new)) - skip)
            if old.get(key, ABSENT) != new.get(key, ABSENT)]


def _list_change(field: str, old: list, new: list) -> str:
    gone, came = [v for v in old if v not in new], [v for v in new if v not in old]
    if not (gone or came):
        return f"{field} reordered" if len(old) == len(new) else f"{field} {len(old)} → {len(new)} items"
    if len(gone) == len(came) and all(isinstance(v, dict) for v in gone + came):
        # As many records changed as went: each, in order, by key (a renamed step of a dynamic view).
        at = [next(i for i, v in enumerate(new) if v is n) for n in came]
        return "; ".join(_field_change(f"{field}[{i}]", o, n) for i, o, n in zip(at, gone, came, strict=True))
    return f"{field} " + " ".join([f"+{_short(v)}" for v in came] + [f"−{_short(v)}" for v in gone])


def _field_change(field: str, old, new) -> str:
    """What changed in one field: per key in a dict, the items added and removed in a list, else old → new."""
    if isinstance(old, dict) and isinstance(new, dict):
        return "; ".join(_field_change(f"{field}.{key}", was, now) for key, was, now in _differing(old, new))
    if isinstance(old, list) and isinstance(new, list):
        return _list_change(field, old, new)
    was, now = _pair(old, new)
    return f"{field} {was} → {now}"


def changes(old: dict, new: dict, skip: frozenset[str] = frozenset()) -> list[str]:
    """One text per field that differs, in field order; id and the fields in `skip` aside."""
    return [_field_change(key, was, now) for key, was, now in _differing(old, new, skip | {"id"})]


def _label(record: dict) -> str:
    kind = record.get("kind") or record.get("type") or record.get("element")
    title = record.get("title")
    shown_title = title and not ("source" in record and record["id"].endswith(f": {title}"))  # an arrow's id holds it
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


def _checked(model, side: str) -> dict | None:
    """`model` when it has the shape the diff reads (see the module docstring), else a ValueError."""
    if model is None:
        return None
    if not isinstance(model, dict) or not isinstance(model.get("specification") or {}, dict):
        raise ValueError(f"the {side} model is not a modelo.json object")  # noqa: TRY004 -- bad data, one error type with the JSON errors main turns into a message
    for _, path in SECTIONS:
        node = model
        for key in path[:-1]:
            node = node.get(key, {})
            if not isinstance(node, dict):
                raise ValueError(f"the {side} model's {key} is not an object")  # noqa: TRY004 -- bad data, as above
        rows = node.get(path[-1], [])
        need = ("id", "source", "target") if path in ARROWS else ("id",)
        if not isinstance(rows, list) or not all(isinstance(r, dict) and all(isinstance(r.get(k), str) for k in need)
                                                 for r in rows):
            raise ValueError(f"the {side} model's {'.'.join(path)} is not a list of records with {', '.join(need)}")
        if len({r["id"] for r in rows}) != len(rows):
            raise ValueError(f"the {side} model's {'.'.join(path)} has an id twice")
    return model


def markdown(base: dict | None, pr: dict | None) -> str:
    """The comment's Markdown: what `pr` changes against `base` (None: no model); "" when nothing."""
    base, pr = _checked(base, "base"), _checked(pr, "PR")
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
    try:  # json's errors are ValueErrors too: a message, not a traceback, in CI
        base = json.loads(text) if text.strip() else None
    except ValueError as error:
        raise SystemExit(f"scripts/arquitetura/diff.py: the base model is not JSON: {error}") from error
    try:
        pr = modelo.load() if modelo.SAIDA.exists() else None
    except ValueError as error:
        raise SystemExit(f"scripts/arquitetura/diff.py: the PR model ({modelo.SAIDA}) is not JSON: {error}") from error
    try:
        out = markdown(base, pr)
    except ValueError as error:
        raise SystemExit(f"scripts/arquitetura/diff.py: {error}") from error
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
