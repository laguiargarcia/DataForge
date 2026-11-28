"""arquitetura/modelo.json: the LikeC4 model of arquitetura/*.c4 as a small, sorted JSON (df-8x0m.23.43).

The checks that read the C4 model (the bead hook, the CI checks, the PR comment) read this file in
Python instead of running LikeC4: the hook cannot run npx on every `bd create`. The CI job
`arquitetura` runs `conferir`, so the committed file is always the export of the committed .c4.

    python3 scripts/arquitetura/modelo.py gerar      # rewrite arquitetura/modelo.json from the .c4
    python3 scripts/arquitetura/modelo.py conferir   # exit 1 when it is stale (with a diff), a
                                                     # view edge is ambiguous (arestas_ambiguas) or
                                                     # hoje-conteineres breaks README rule 5 (regra5)

The command line takes no paths: it always reads arquitetura/ and writes arquitetura/modelo.json
(FONTE and SAIDA), so no argument reaches the npx call or a file write. The functions take other
paths, for the tests. Only the standard library; LikeC4 runs through npx at LIKEC4 (the same
version as arquitetura/README.md, docs/commands.md and LIKEC4_VERSION of the CI job). The validate
fails on any warning (--max-warnings 0), as in the CI job.

API, for the other scripts (put scripts/ on sys.path, as scripts/beads/check.py does):

    from arquitetura import modelo
    m = modelo.load()            # or modelo.load(path): the dict below, read from the JSON file
    modelo.HOJE, modelo.ALVO     # "Hoje", "Alvo": the top-level id suffix of today's and the target model
    modelo.sistema(id)           # the top-level segment of an element id ("casaAlvo.servidorAlvo" -> "casaAlvo")
    modelo.e_hoje(id)            # whether that segment ends in HOJE: the element is today's model
    modelo.MARCAS                # "novo", "muda", "sai": the target's marks, in the views' tag-rule order
    modelo.marca(relation)       # the mark of a relation of the export, None when it has none
    modelo.regra5(raw)           # README rule 5 on the view hoje-conteineres of a `likec4 export json`

Layout of the dict. Every key is always present; an absent text is null, an absent list [] and
absent metadata {}. Lists of records are sorted by "id", tags are sorted, links keep source order.

    likec4         the LikeC4 version that exported it
    specification  {"elements", "tags", "relationships", "deployments"}: sorted lists of the kind
                   and tag names spec.c4 declares
    elements       [{id, kind, title, description, technology, tags, metadata, links}]
                   id is the full name ("casaAlvo.servidorAlvo"); the parent is the id minus its
                   last part. links: [{url, title}] (title null when not given)
    relations      [{id, source, target, title, kind, description, technology, tags, metadata, links}]
                   source and target are element ids; kind is null or a relationship kind
                   ("leitura"). id is "<source> -> <target>: <title>", or "<source> -> <target>"
                   for an arrow without a title: LikeC4's own relation id depends on the position
                   in the file, so adding one relation would rename every relation after it. Two
                   arrows with the same id stop gerar (ValueError): give them distinct titles.
                   Renaming an arrow changes its id, so a diff sees it removed and added
    views          [{id, type, title, description, tags, links, viewOf, steps}]
                   type is "element", "dynamic" or "deployment"; viewOf is the element of a
                   `view x of y`, else null; steps, for a dynamic view, is [{source, target, title}]
                   in order, else []
    deployment     {"nodes": [{id, kind, title, description, technology, tags, metadata, links}],
                    "instances": [{id, element, title, description, technology, tags, metadata,
                                   links}],
                    "relations": [same fields as relations, between node or instance ids]}
                   an instance's title is its element's when it sets none

Dropped from `likec4 export json`: layout, styles, colours, notation, shapes, the views' nodes and
edges (what a view draws follows from the model and its rules), hashes, source paths, the local
absolute path of relative links, LikeC4's relation ids, project, imports and manual layouts.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

LIKEC4 = "1.61.1"
REPO = Path(__file__).resolve().parents[2]
FONTE = REPO / "arquitetura"
SAIDA = FONTE / "modelo.json"
VIEW_TYPES = ("element", "dynamic", "deployment")
# Suffix of every top-level element id: the model it belongs to. Today's model is hoje.c4 and the
# files that extend its elements (componentes-*.c4); the export keeps no source file (df-8x0m.23.46).
HOJE, ALVO = "Hoje", "Alvo"
# The marks of the target (README, "Marcas do alvo"), in the order the views' tag rules list them:
# the last rule that matches a joined relation gives an edge its colour. SEM_MARCA is the colour of
# an unmarked relation (LikeC4's default).
MARCAS = ("novo", "muda", "sai")
SEM_MARCA = "gray"
COMPONENTE = "componente"  # the element kind of spec.c4 for a component (README, component rules)


def sistema(id_) -> str:
    """The top-level segment of an element id: the system the element belongs to."""
    return str(id_).split(".", 1)[0]


def e_hoje(id_) -> bool:
    """Whether an element id belongs to today's model: only the top-level segment counts."""
    return sistema(id_).endswith(HOJE)


def load(path: Path | str | None = None) -> dict:
    """The model written by `gerar`, from `path` (default arquitetura/modelo.json)."""
    return json.loads(Path(path or SAIDA).read_text(encoding="utf-8"))


def _likec4(*args: str) -> None:
    npx = shutil.which("npx")
    if npx is None:
        raise SystemExit("npx not found: install Node (arquitetura/README.md)")
    run = subprocess.run([npx, "--yes", f"likec4@{LIKEC4}", *args], capture_output=True, text=True,
                         env=os.environ | {"NO_COLOR": "1"}, check=False)
    if run.returncode != 0:
        raise SystemExit(f"likec4 {args[0]} failed (exit {run.returncode}):\n{run.stdout}{run.stderr}")


def exportar(fonte: Path) -> dict:
    """`likec4 export json` of the folder `fonte`, without layout. Validates first: on an invalid
    model the export still exits 0, leaving out what it could not read (whole views)."""
    _likec4("validate", "--max-warnings", "0", str(fonte))
    with tempfile.TemporaryDirectory() as tmp:
        saida = Path(tmp) / "likec4.json"
        _likec4("export", "json", "--skip-layout", "-o", str(saida), str(fonte))
        return json.loads(saida.read_text(encoding="utf-8"))


def _text(value) -> str | None:
    """A LikeC4 text: a plain string, or {"txt": ...} / {"md": ...}."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("txt", "md"):
        return next(iter(value.values()))
    raise ValueError(f"unknown LikeC4 text: {value!r}")


def _ref(value) -> str:
    """A relation end: {"model": id} or {"deployment": id}."""
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("model", "deployment"):
        return next(iter(value.values()))
    raise ValueError(f"unknown LikeC4 relation end: {value!r}")


def _links(raw: dict) -> list[dict]:
    return [{"url": link["url"], "title": link.get("title")} for link in raw.get("links") or []]


def _common(raw: dict) -> dict:
    return {"title": raw.get("title"), "description": _text(raw.get("description")),
            "technology": raw.get("technology"), "tags": sorted(raw.get("tags") or []),
            "metadata": dict(raw.get("metadata") or {}), "links": _links(raw)}


def _relations(raws) -> list[dict]:
    rows = [{"source": _ref(r["source"]), "target": _ref(r["target"]), "kind": r.get("kind"), **_common(r)}
            for r in raws]
    for row in rows:
        row["title"] = row["title"] or ""
        row["id"] = f"{row['source']} -> {row['target']}" + (f": {row['title']}" if row["title"] else "")
    repeated = sorted(rid for rid, n in Counter(r["id"] for r in rows).items() if n > 1)
    if repeated:
        raise ValueError(f"two arrows with the same source, target and title: {', '.join(repeated)}; "
                         "give them distinct titles in the .c4")
    return sorted(rows, key=lambda r: r["id"])


def _view(raw: dict) -> dict:
    kind = raw["_type"]
    if kind not in VIEW_TYPES:
        raise ValueError(f"unknown LikeC4 view type: {kind!r}")
    steps = [{"source": e["source"], "target": e["target"], "title": e.get("label") or ""}
             for e in raw.get("edges") or []] if kind == "dynamic" else []
    return {"id": raw["id"], "type": kind, "title": raw.get("title"), "description": _text(raw.get("description")),
            "tags": sorted(raw.get("tags") or []), "links": _links(raw), "viewOf": raw.get("viewOf"),
            "steps": steps}


def _aresta_ambigua(view: str, edge: dict, rels: dict, cores: dict | None = None) -> list[str]:
    """What one edge of an element or deployment view hides from a reader (README rule 3). LikeC4
    joins the relations between a node's descendants into one edge: with distinct titles it shows
    '[...]', or one of them and none of the rest, unless the view gives a title (`with { title }`)
    or a relation goes straight between the two nodes; with distinct kinds it takes one of them.
    The export keeps no view rules, so a view title is known only by being none of the joined
    titles: one equal to a joined title is flagged as if LikeC4 had picked it. With no label over
    several titles the edge comes out untitled and hides them (df-w7nk). Relations of different
    marks also need the right colour (_cor_ambigua, df-8x0m.23.47.17)."""
    try:
        joined = [rels[r] for r in edge.get("relations") or []]
    except KeyError as missing:
        raise ValueError(f"view {view}: edge {edge.get('id')} names an unknown relation {missing}") from None
    where = f"view {view}: {edge['source']} -> {edge['target']}"
    label = edge.get("label") or ""
    titles = {r.get("title") or "" for r in joined}
    direct = {r.get("title") or "" for r in joined
              if (_ref(r["source"]), _ref(r["target"])) == (edge["source"], edge["target"])}
    fix = f"include {edge['source']} -> {edge['target']} with {{ title '...' }}"
    problems = []
    if label == "[...]":
        problems.append(f"{where} is drawn as '[...]': give it one title in the view ({fix})")
    elif len(titles) > 1 and not label and "" not in direct:
        hidden = ", ".join(repr(t) for t in sorted(titles - {""}))
        problems.append(f"{where} joins {len(joined)} relations and comes out with no title, hiding {hidden}: "
                        f"give it a title of its own in the view ({fix}), or draw an arrow straight between "
                        "the two")
    elif len(titles) > 1 and label in titles and label not in direct:
        problems.append(f"{where} joins {len(joined)} relations and shows only {label!r}: give it a title "
                        f"of its own in the view, none of the joined ones ({fix}), or draw an arrow "
                        "straight between the two")
    kinds = sorted({r.get("kind") or "(none)" for r in joined})
    if len(kinds) > 1:
        problems.append(f"{where} joins relations of kinds {', '.join(kinds)} and is drawn as "
                        f"{edge.get('kind') or '(none)'}: give the arrows behind it one kind, or exclude the odd one "
                        f"from the view (exclude <child> -> <target>)")
    return problems + _cor_ambigua(where, edge, joined, cores or {})


def marca(relation: dict) -> str | None:
    """The mark of the target (MARCAS) a relation carries, None when it has none."""
    return next((t for t in MARCAS if t in (relation.get("tags") or ())), None)


def _cor_ambigua(where: str, edge: dict, joined: list[dict], cores: dict) -> list[str]:
    """An edge joining relations of different marks (README, component rules). The view's tag rules
    (`include * -> * where tag is #x with { color }`, in the order of MARCAS) give it the colour of
    the last mark that any joined relation has. The colour must be the one of the arrow straight
    between the two nodes, or one the view gives after the tag rules; with no direct arrow, a
    colour given by the view is known by not being the one the tag rules pick."""
    marks = {marca(r) for r in joined}
    if len(marks) <= 1:
        return []
    color = edge.get("color")
    direct = {cores.get(marca(r), SEM_MARCA) for r in joined
              if (_ref(r["source"]), _ref(r["target"])) == (edge["source"], edge["target"])}
    named = ", ".join(f"#{m}" if m else "(no mark)" for m in sorted(marks, key=lambda m: MARCAS.index(m) if m else -1))
    fix = f"include {edge['source']} -> {edge['target']} with {{ color ... }}"
    if direct:
        if color in direct:
            return []
        want = " or ".join(sorted(direct))
        fix = f"include {edge['source']} -> {edge['target']} with {{ color {min(direct)} }}"
        return [f"{where} joins relations marked {named} and is drawn {color}, not {want}, the colour of the "
                f"arrow straight between the two: give it that colour after the tag rules ({fix})"]
    picked = cores.get(next(m for m in reversed(MARCAS) if m in marks), SEM_MARCA)
    if color != picked:
        return []
    return [f"{where} joins relations marked {named} and is drawn {color}, the colour the tag rules pick from "
            f"one of them: give it the colour of the edge after the tag rules ({fix}), or draw an arrow "
            "straight between the two"]


def regra5(raw: dict, view: str = "hoje-conteineres") -> list[str]:
    """README rule 5: in `view`, a pair of nodes has a direct arrow only over more than one arrow
    between their descendants, counting the ones the view excludes; a pair of several with no direct
    arrow gets a title from the view (known, as in rule 3, by being none of the joined titles)."""
    vista = (raw.get("views") or {}).get(view)
    if vista is None:
        return []
    nodes = [n["id"] for n in vista.get("nodes") or []]

    def node(id_: str) -> str | None:
        owners = [n for n in nodes if id_ == n or id_.startswith(n + ".")]
        return max(owners, key=len) if owners else None

    pairs: dict[tuple[str, str], list[dict]] = {}
    for r in (raw.get("relations") or {}).values():
        a, b = node(_ref(r["source"])), node(_ref(r["target"]))
        if a and b and a != b:
            pairs.setdefault((a, b), []).append(r)
    labels = {(e["source"], e["target"]): e.get("label") or "" for e in vista.get("edges") or []}
    componentes = [e["id"] for e in (raw.get("elements") or {}).values() if e.get("kind") == COMPONENTE]

    def tem_componentes(n: str) -> bool:
        return any(c.startswith(n + ".") for c in componentes)

    problems = []
    for (a, b), rels in sorted(pairs.items()):
        direct = [r for r in rels if (_ref(r["source"]), _ref(r["target"])) == (a, b)]
        inner = [r for r in rels if r not in direct]
        if direct and not inner and (tem_componentes(a) or tem_componentes(b)):
            problems.append(f"view {view}: {a} -> {b} has a direct arrow and no component arrow, though "
                            f"{a if tem_componentes(a) else b} has components: draw the arrow from or to the "
                            "component that does it (README rule 5)")
        elif direct and len(inner) == 1:
            problems.append(f"view {view}: {a} -> {b} has a direct arrow over one component arrow "
                            f"({inner[0].get('title') or ''!r}): remove the direct arrow, the view shows that one's "
                            "title (README rule 5)")
        elif not direct and len(inner) > 1 and (a, b) in labels \
                and labels[(a, b)] in {r.get("title") or "" for r in inner}:
            problems.append(f"view {view}: {a} -> {b} joins {len(inner)} component arrows with no direct arrow: "
                            "draw one in hoje.c4 whose title sums them up (README rule 5)")
    return problems


def arestas_ambiguas(raw: dict) -> list[str]:
    """Every edge of a `likec4 export json` view that a reader cannot take at its word: drawn as
    '[...]', showing one of several joined titles or none of them, joining relations of different
    kinds, or joining different marks with a colour that is neither the direct arrow's nor the
    view's. A mark's colour is its tag colour in spec.c4, which must be the one the views' tag
    rules give. The steps of a dynamic view are drawn one by one, with their own titles: not
    checked."""
    rels = raw.get("relations", {}) | raw.get("deployments", {}).get("relations", {})
    cores = {tag: spec["color"] for tag, spec in (raw.get("specification", {}).get("tags") or {}).items()
             if tag in MARCAS and (spec or {}).get("color")}
    return [problem for vid, view in sorted(raw.get("views", {}).items()) if view.get("_type") != "dynamic"
            for edge in view.get("edges") or [] for problem in _aresta_ambigua(vid, edge, rels, cores)]


def normalizar(raw: dict) -> dict:
    """The modelo.json dict (see the module docstring) of one `likec4 export json`."""
    spec = raw.get("specification", {})
    elements = sorted(({"id": e["id"], "kind": e["kind"], **_common(e)} for e in raw.get("elements", {}).values()),
                      key=lambda e: e["id"])
    titles = {e["id"]: e["title"] for e in elements}
    deployed = raw.get("deployments", {})
    nodes, instances = [], []
    for d in deployed.get("elements", {}).values():
        if "element" in d:
            row = {"id": d["id"], "element": d["element"], **_common(d)}
            row["title"] = row["title"] or titles.get(d["element"])
            instances.append(row)
        else:
            nodes.append({"id": d["id"], "kind": d["kind"], **_common(d)})
    return {
        "likec4": LIKEC4,
        "specification": {key: sorted(spec.get(key, {})) for key in ("elements", "tags", "relationships", "deployments")},
        "elements": elements,
        "relations": _relations(raw.get("relations", {}).values()),
        "views": sorted((_view(v) for v in raw.get("views", {}).values()), key=lambda v: v["id"]),
        "deployment": {"nodes": sorted(nodes, key=lambda n: n["id"]),
                       "instances": sorted(instances, key=lambda n: n["id"]),
                       "relations": _relations(deployed.get("relations", {}).values())},
    }


def texto(modelo: dict) -> str:
    """The file's text: sorted keys, two-space indent, UTF-8 as is, one final newline."""
    return json.dumps(modelo, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def gerar(fonte: Path = FONTE, saida: Path = SAIDA) -> None:
    saida.write_text(texto(normalizar(exportar(fonte))), encoding="utf-8")


def conferir(fonte: Path = FONTE, saida: Path = SAIDA) -> list[str]:
    """The problems of the .c4, one line each: every ambiguous view edge (`arestas_ambiguas`), every
    pair of hoje-conteineres against README rule 5 (`regra5`), then, when the committed file is
    stale, a line saying so and the diff to the export. Empty when fine."""
    raw = exportar(fonte)
    novo = texto(normalizar(raw))
    velho = saida.read_text(encoding="utf-8") if saida.exists() else ""
    diff = list(difflib.unified_diff(velho.splitlines(), novo.splitlines(), str(saida), "export of the .c4",
                                     lineterm=""))
    stale = [f"{saida} is stale: run python3 scripts/arquitetura/modelo.py gerar and commit it"] if diff else []
    return arestas_ambiguas(raw) + regra5(raw) + stale + diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("acao", choices=("gerar", "conferir"))
    acao = parser.parse_args(argv).acao
    try:
        if acao == "gerar":
            gerar(FONTE, SAIDA)
            return 0
        problems = conferir(FONTE, SAIDA)
    except ValueError as error:  # a model normalizar refuses: the message, not a traceback, in CI
        raise SystemExit(str(error)) from error
    if problems:
        print("\n".join(problems))
        return 1
    print(f"{SAIDA} is the export of {FONTE}, and every view edge reads at its word")
    return 0


if __name__ == "__main__":
    sys.exit(main())
