"""Marks of the v2 target (df-8x0m.23.37): every element or relation of arquitetura/alvo.c4 that
differs from hoje.c4 carries one tag, #novo (does not exist yet), #muda (exists and will change) or
#sai (exists today and will go), and `metadata { bead 'df-...' }` with the open bead of that change
(one id or a list of them). When the bead closes, the mark goes, or moves to the bead that carries
the change on (a decision bead hands it to its implementation beads).

Two halves, because CI has no bd (beads are local):

- `marcas.py [modelo.json]` (CI): fails when a marked item has no bead, a malformed id or two of the
  three tags, or when an item carries a bead and no mark.
- `marcas.py --bd [modelo.json]` (local, with bd): also fails when a marked bead is closed or does
  not exist. The close hook (scripts/beads/check.py) refuses `bd close` of a bead origin/main still
  marks, with `marked_beads`.

Exit 0 when clean, 1 with the problems on stdout, 2 when bd cannot be asked.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

TAGS = ("novo", "muda", "sai")
KEY = "bead"
BEAD_ID = re.compile(r"^df-[a-z0-9]+(?:\.\d+)*$")
MODEL = Path(__file__).resolve().parents[2] / "arquitetura" / "modelo.json"
BD_TIMEOUT = 20


class BdUnavailable(RuntimeError):
    """bd is not installed or did not answer."""


def _items(model: dict) -> Iterator[tuple[str, dict]]:
    """Every item that may carry a mark, with its id: elements, relations ("<source> -> <target>:
    <title>"), and the deployment nodes, instances and relations."""
    deployment = model.get("deployment") or {}
    for group in (model.get("elements"), model.get("relations"), deployment.get("nodes"),
                  deployment.get("instances"), deployment.get("relations")):
        for item in group or ():
            yield str(item.get("id")), item


def _beads(item: dict) -> list | None:
    """The ids under metadata `bead` as written (a string becomes a list of one); None when absent."""
    value = (item.get("metadata") or {}).get(KEY)
    if value is None:
        return None
    return value if isinstance(value, list) else [value]


def _item_problems(label: str, item: dict) -> list[str]:
    tags = sorted(t for t in item.get("tags") or () if t in TAGS)
    beads = _beads(item)
    if len(tags) > 1:
        return [f"{label}: tem {' e '.join('#' + t for t in tags)}; fica com uma só de #novo, #muda, #sai"]
    if not tags:
        if beads is not None:
            return [f"{label}: metadata {KEY} sem marca; ponha #novo, #muda ou #sai, ou tire o {KEY}"]
        return []
    if not beads:
        return [f"{label}: #{tags[0]} sem metadata {{ {KEY} 'df-...' }} com o bead aberto da mudança"]
    bad = [b for b in beads if not (isinstance(b, str) and BEAD_ID.match(b))]
    if bad:
        return [f"{label}: id de bead malformado {', '.join(repr(b) for b in bad)} (esperado 'df-...')"]
    return []


def problems(model: dict) -> list[str]:
    """What CI checks: each mark has exactly one tag and well-formed bead ids."""
    return [p for label, item in _items(model) for p in _item_problems(label, item)]


def marked_beads(model: dict) -> dict[str, list[str]]:
    """Each well-formed bead id of a marked item, with the items it marks, in model order."""
    out: dict[str, list[str]] = {}
    for label, item in _items(model):
        if not any(t in TAGS for t in item.get("tags") or ()):
            continue
        for bead in _beads(item) or ():
            if isinstance(bead, str) and BEAD_ID.match(bead):
                out.setdefault(bead, []).append(label)
    return out


def bd_problems(model: dict, status: Callable[[str], str | None]) -> list[str]:
    """Marks whose bead is closed or unknown; `status` answers a bead's status, None if unknown."""
    found = []
    for bead, labels in marked_beads(model).items():
        state = status(bead)
        where = "; ".join(labels)
        if state is None:
            found.append(f"{bead} não existe no bd e marca {where}")
        elif state == "closed":
            found.append(f"{bead} está fechado e ainda marca {where}: tire a marca ou passe-a para o "
                         "bead que continua a mudança")
    return found


def bd_status(bead: str) -> str | None:
    """The bead's status from bd, None when bd does not know it."""
    try:
        r = subprocess.run(["bd", "show", "--json", "--readonly", "--", bead], capture_output=True,
                           text=True, timeout=BD_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BdUnavailable(str(exc)) from exc
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout or "[]")
    return data[0].get("status") if isinstance(data, list) and data else None


def main(argv: list[str]) -> int:
    use_bd = "--bd" in argv
    paths = [a for a in argv if a != "--bd"]
    model = json.loads(Path(paths[0] if paths else MODEL).read_text(encoding="utf-8"))
    found = problems(model)
    if use_bd:
        try:
            found += bd_problems(model, bd_status)
        except BdUnavailable as exc:
            sys.stderr.write(f"marcas: não consegui perguntar ao bd ({exc})\n")
            return 2
    if found:
        sys.stdout.write("Marcas do alvo (arquitetura/alvo.c4, df-8x0m.23.37):\n- " + "\n- ".join(found) + "\n")
        return 1
    sys.stdout.write("marcas: ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
