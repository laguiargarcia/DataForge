"""Marks of the v2 target (df-8x0m.23.37): every element or relation of arquitetura/alvo.c4 that
differs from hoje.c4 carries one tag, #novo (does not exist yet), #muda (exists and will change) or
#sai (exists today and will go), and `metadata { bead 'df-...' }` with the open bead of that change
(one id or a list of them). When the bead closes, the mark goes, or moves to the bead that carries
the change on (a decision bead hands it to its implementation beads).

Two halves, because CI has no bd (beads are local):

- `marcas.py` (CI): fails when a marked item has no bead, a malformed id or two of the
  three tags, or when an item carries a bead and no mark. Also when a mark is on today's model:
  an element whose top-level id ends in `Hoje`, a relation with an end there, or a deployment
  instance of such an element (df-8x0m.23.46, df-8x0m.23.48; the export keeps no source file, so
  the suffix says the model: modelo.e_hoje), and when a top-level element ends in neither `Hoje`
  nor `Alvo`. Deployment nodes and deployment relations stay outside this rule: their ids are
  deployment ids, with no model suffix.
- `marcas.py --bd` (local, with bd): also fails when a marked bead is closed or does
  not exist. The close hook (scripts/beads/check.py) refuses `bd close` of a bead origin/main still
  marks, with `marked_beads`.

Both read arquitetura/modelo.json through modelo.load(); no argument names a path. Exit 0 when
clean, 1 with the problems on stdout, 2 when bd cannot be asked or an argument is unknown.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: arquitetura.modelo

from arquitetura import modelo

TAGS = modelo.MARCAS
COMPONENTE = "componente"
# Target ids (last segment) whose today twin is not the id with Hoje in place of Alvo (README,
# component rules): two component ids already taken by a target container or database, the re-cut
# of the API, and the target's containers and database that today has under another name.
GEMEOS = {"nucleoAlvo": "servidorHoje", "historicoRunsAlvo": "historicoHoje", "rotaTabelasAlvo": "apiHoje",
          "appsAlvo": "apiHoje", "historicoAlvo": "dbDataforgeHoje", "bancoAppAlvo": "bancoNinhoHoje",
          "appCelularAlvo": "telasNinhoHoje"}
KEY = "bead"
BEAD_ID = re.compile(r"df-[a-z0-9]+(?:\.\d+)*")  # used with fullmatch: no trailing newline
BD_TIMEOUT = 20


NOT_FOUND = "no issue"  # what bd says, on stderr and in the JSON error, for an unknown id


class BdUnavailable(RuntimeError):
    """bd is not installed, failed or answered something that is not its JSON."""


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
    bad = [b for b in beads if not (isinstance(b, str) and BEAD_ID.fullmatch(b))]
    if bad:
        return [f"{label}: id de bead malformado {', '.join(repr(b) for b in bad)} (esperado 'df-...')"]
    return []


def _today_problems(model: dict) -> list[str]:
    """Marks on today's model, and top-level elements that belong to neither model (df-8x0m.23.46).
    The export keeps no source file, so the model an item belongs to is its top-level id's suffix:
    an element's own id, a relation's ends, a deployment instance's element. Deployment nodes and
    relations have deployment ids, with no suffix, and stay outside (df-8x0m.23.48)."""
    hoje, alvo = modelo.HOJE, modelo.ALVO
    tops = dict.fromkeys(modelo.sistema(e.get("id")) for e in model.get("elements") or ())
    found = [f"elemento de topo {top!r}: o id termina em {hoje} (modelo de hoje: hoje.c4 e os arquivos que "
             f"estendem seus elementos) ou {alvo} (alvo.c4), para a conferência saber de qual modelo ele é"
             for top in tops if not top.endswith((hoje, alvo))]
    deployment = model.get("deployment") or {}
    for items, ends, instances in ((model.get("elements"), ("id",), False),
                                   (model.get("relations"), ("source", "target"), False),
                                   (deployment.get("instances"), ("element",), True)):
        for item in items or ():
            tags = [t for t in item.get("tags") or () if t in TAGS]
            if tags and any(modelo.e_hoje(item.get(end)) for end in ends):
                label = f"{item.get('id')} (instância de {item.get('element')})" if instances else item.get("id")
                found.append(f"{label}: #{tags[0]} no modelo de hoje; as marcas ficam só no alvo "
                             f"(ids sob um elemento de topo ...{alvo})")
    return found


def _gemeo(id_: str, hoje: dict[str, str]) -> str | None:
    """The today element of a target element id: the today element whose last segment is the target's
    with HOJE in place of ALVO (or its GEMEOS entry). None when there is no single one."""
    leaf = id_.rsplit(".", 1)[-1]
    name = GEMEOS.get(leaf) or (leaf.removesuffix(modelo.ALVO) + modelo.HOJE if leaf.endswith(modelo.ALVO) else None)
    return hoje.get(name) if name else None


def _twin_problems(model: dict) -> list[str]:
    """An unmarked component arrow of the target (an end is a component) is today's arrow as it is
    (README, "Marcas do alvo"): today has the arrow between the twins of its ends, with the same title
    and codigo (df-8x0m.23.47.20). Container arrows stay out: today keeps a container arrow only as
    the summary of several component arrows (README rule 5), the target keeps its own as the plan."""
    elements = model.get("elements") or ()
    componentes = {e.get("id") for e in elements if e.get("kind") == COMPONENTE}
    leaves: dict[str, list[str]] = {}
    for e in elements:
        if modelo.e_hoje(e.get("id")):
            leaves.setdefault(str(e.get("id")).rsplit(".", 1)[-1], []).append(e.get("id"))
    hoje = {leaf: ids[0] for leaf, ids in leaves.items() if len(ids) == 1}
    today = {(r.get("source"), r.get("target"), r.get("title") or ""): r for r in model.get("relations") or ()
             if modelo.e_hoje(r.get("source"))}
    found = []
    for r in model.get("relations") or ():
        source, target = r.get("source"), r.get("target")
        if modelo.e_hoje(source) or any(t in TAGS for t in r.get("tags") or ()) \
                or not {source, target} & componentes:
            continue
        twin = (_gemeo(source, hoje), _gemeo(target, hoje), r.get("title") or "")
        codigo = (r.get("metadata") or {}).get("codigo")
        match = today.get(twin) if None not in twin else None
        if match is None:
            found.append(f"{r.get('id')}: seta sem marca sem par no hoje ({twin[0]} -> {twin[1]} com o mesmo "
                         "título); ponha a marca da mudança ou copie o título e o codigo da seta de hoje")
        elif (match.get("metadata") or {}).get("codigo") != codigo:
            found.append(f"{r.get('id')}: seta sem marca com codigo diferente do da seta de hoje {match.get('id')}; "
                         "ponha a marca da mudança ou copie o codigo de hoje")
    return found


def problems(model: dict) -> list[str]:
    """What CI checks: each mark has exactly one tag and well-formed bead ids, only the target
    carries marks, and every unmarked component arrow of the target mirrors one of today."""
    return ([p for label, item in _items(model) for p in _item_problems(label, item)] + _today_problems(model)
            + _twin_problems(model))


def marked_beads(model: dict) -> dict[str, list[str]]:
    """Each well-formed bead id of a marked item, with the items it marks, in model order."""
    out: dict[str, list[str]] = {}
    for label, item in _items(model):
        if not any(t in TAGS for t in item.get("tags") or ()):
            continue
        for bead in _beads(item) or ():
            if isinstance(bead, str) and BEAD_ID.fullmatch(bead):
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
        if NOT_FOUND in r.stderr or NOT_FOUND in r.stdout:
            return None
        raise BdUnavailable(f"bd show {bead} saiu com {r.returncode}: {r.stderr.strip()[:200]}")
    try:
        data = json.loads(r.stdout)
    except ValueError as exc:
        raise BdUnavailable(f"bd show {bead} não devolveu JSON") from exc
    if not (isinstance(data, list) and data and isinstance(data[0], dict)):
        raise BdUnavailable(f"bd show {bead} devolveu um JSON inesperado")
    return data[0].get("status")


def main(argv: list[str]) -> int:
    unknown = [a for a in argv if a != "--bd"]
    if unknown:
        sys.stderr.write(f"marcas: argumento desconhecido {unknown[0]!r}; uso: marcas.py [--bd]\n")
        return 2
    use_bd = bool(argv)
    model = modelo.load()
    found = problems(model)
    if use_bd:
        try:
            found += bd_problems(model, bd_status)
        except BdUnavailable as exc:
            _report(found)
            sys.stderr.write(f"marcas: não consegui perguntar ao bd ({exc})\n")
            return 2
    if _report(found):
        return 1
    sys.stdout.write("marcas: ok\n")
    return 0


def _report(found: list[str]) -> bool:
    if found:
        sys.stdout.write("Marcas do alvo (arquitetura/alvo.c4, df-8x0m.23.37):\n- " + "\n- ".join(found) + "\n")
    return bool(found)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
