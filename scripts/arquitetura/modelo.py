"""arquitetura/modelo.json: the LikeC4 model of arquitetura/*.c4 as a small, sorted JSON (df-8x0m.23.43).

The checks that read the C4 model (the bead hook, the CI checks, the PR comment) read this file in
Python instead of running LikeC4: the hook cannot run npx on every `bd create`. The CI job
`arquitetura` runs `conferir`, so the committed file is always the export of the committed .c4.

    python3 scripts/arquitetura/modelo.py gerar      # rewrite arquitetura/modelo.json from the .c4
    python3 scripts/arquitetura/modelo.py conferir   # exit 1, with a diff, when it is stale

The command line takes no paths: it always reads arquitetura/ and writes arquitetura/modelo.json
(FONTE and SAIDA), so no argument reaches the npx call or a file write. The functions take other
paths, for the tests. Only the standard library; LikeC4 runs through npx at LIKEC4 (the same
version as arquitetura/README.md and LIKEC4_VERSION of the CI job). The validate fails on any
warning (--max-warnings 0), as in the CI job.

API, for the other scripts (put scripts/ on sys.path, as scripts/beads/check.py does):

    from arquitetura import modelo
    m = modelo.load()            # or modelo.load(path): the dict below, read from the JSON file

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
    """The diff from the committed file to the export of the .c4; empty when it is fresh."""
    novo = texto(normalizar(exportar(fonte)))
    velho = saida.read_text(encoding="utf-8") if saida.exists() else ""
    return list(difflib.unified_diff(velho.splitlines(), novo.splitlines(), str(saida), "export of the .c4", lineterm=""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("acao", choices=("gerar", "conferir"))
    acao = parser.parse_args(argv).acao
    try:
        if acao == "gerar":
            gerar(FONTE, SAIDA)
            return 0
        diff = conferir(FONTE, SAIDA)
    except ValueError as error:  # a model normalizar refuses: the message, not a traceback, in CI
        raise SystemExit(str(error)) from error
    if diff:
        print(f"{SAIDA} is stale: run python3 scripts/arquitetura/modelo.py gerar and commit it")
        print("\n".join(diff))
        return 1
    print(f"{SAIDA} is the export of {FONTE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
