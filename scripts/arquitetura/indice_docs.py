"""docs/INDEX.md, generated (df-8x0m.23.39.2): the doc of every box of the C4 model with its
read_when, then the transversal docs, then the frozen ADRs.

    python3 scripts/arquitetura/indice_docs.py gerar     # rewrite docs/INDEX.md
    python3 scripts/arquitetura/indice_docs.py conferir  # exit 1 when it differs, or a doc is left out

The boxes come from arquitetura/modelo.json through docs.docs_por_elemento(): one table per
system, under a heading per container; a doc is one row per system, in the table of the first box
that links it (today's first), naming every box of the system that links it. The people and external systems in a table of their own. A transversal doc is one whose
front matter says `transversal: true`: it belongs to no box (commands, testing rules, the bead
check...). docs/adr/ is frozen (df-883u): its files get no front matter, and ADRS holds the read
when of each. The history files (HISTORIA) are gitignored and exist only in the main checkout: a
fixed note names them.

`conferir` fails when the committed INDEX is not what `gerar` writes, and names every doc under
docs/ that is in no part: linked by no box, not `transversal: true`, not an ADR and not history.
Only the standard library (python3 -S).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: arquitetura.*

from arquitetura import docs, modelo

REPO = modelo.REPO
INDICE = "docs/INDEX.md"
HISTORIA = ("docs/superpowers", "docs/phases", "docs/gsd-workflow.md", "docs/workbook-handoff.md")
COMANDO = "python3 scripts/arquitetura/indice_docs.py gerar"
FORA = "Pessoas e sistemas externos"
# docs/adr/ is frozen (df-883u, no edits): its read when lives here. A new file there fails conferir.
ADRS = {
    "docs/adr/0001-adr-decision-layer-superpowers-execution-layer.md":
        ("History", "Background only: how the ADR layer was adopted in 2026-06"),
    "docs/adr/0002-lean-solo-dev-environment.md":
        ("History", "Background only: why graph tools, GSD, claude-mem and /sync were retired"),
    "docs/adr/0003-graphify-local-structure.md":
        ("History", "Background only: the local graphify structure report at SessionStart, superseded 2026-11-03"),
    "docs/adr/0004-automated-test-system.md":
        ("In force until df-cyjt", "When designing or building automated tests (generators, mutation, branch map, "
         "test selection, data checks)"),
    "docs/adr/0005-dev-on-linux-disk-windows-is-prod.md":
        ("In force until df-cyjt", "Where to work (clone on ext4 vs the Windows prod folder), making a worktree "
         "with its venv, updating prod, migrating a session, the beads database flip"),
    "docs/adr/0006-architecture-v2-four-pieces.md":
        ("In force until df-cyjt", "When you need the target architecture: the four pieces (house server ninho, "
         "app database, lake, DataForge as orchestrator only), who owns each piece of data, which way "
         "dependencies point, the repository layout with walls"),
}


def _celula(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _historia(path: str) -> bool:
    return any(path == h or path.startswith(h + "/") for h in HISTORIA)


def _todos(raiz: Path) -> list[str]:
    """Every doc under docs/ but the INDEX and the history, as repo-relative paths."""
    out = [f.relative_to(raiz).as_posix() for f in sorted((raiz / "docs").rglob("*.md"))]
    return [p for p in out if p != INDICE and not _historia(p)]


def _front(raiz: Path, path: str) -> dict:
    try:
        return docs.ler_front_matter(raiz / path)
    except (ValueError, OSError):
        return {}


def _transversal(raiz: Path, path: str) -> bool:
    return _front(raiz, path).get("transversal") == "true"


def _chave(eid: str) -> tuple[str, ...]:
    """The id of an element's system and container, without the Hoje/Alvo suffix."""
    return tuple(docs.peca(eid).split(".")[:2])


def _secoes(m: dict, linked: dict[str, str]) -> list[tuple[int, str, list[dict]]]:
    """(heading level, title, elements): a heading per system, a table per container; the people
    and external systems last, in a table of their own."""
    por_id = {e["id"]: e for e in m["elements"]}
    titulos: dict[tuple[str, ...], str] = {}
    grupos: dict[tuple[str, ...], list[dict]] = {}
    for e in sorted(m["elements"], key=lambda e: (not modelo.e_hoje(e["id"]), e["id"])):
        topo = modelo.sistema(e["id"])
        if por_id[topo]["kind"] != "sistema":
            grupos.setdefault((), []).append(e)
            continue
        chave = _chave(e["id"])
        if len(e["id"].split(".")) <= len(chave):  # the system or the container itself names the heading
            titulos.setdefault(chave, e["title"] or e["id"])
        grupos.setdefault(chave, []).append(e)
    _juntar(grupos, linked)
    out = []
    for chave in sorted(k for k in grupos if k):
        if len(chave) == 1:
            out.append((3, titulos[chave], grupos[chave]))
        else:
            if (chave[0],) not in grupos:
                out.append((3, titulos[(chave[0],)], []))
            if grupos[chave]:  # a container whose rows all went to another one's table
                out.append((4, titulos[chave], grupos[chave]))
    return out + ([(3, FORA, grupos[()])] if () in grupos else [])


def _juntar(grupos: dict[tuple[str, ...], list[dict]], linked: dict[str, str]) -> None:
    """Move every box of a system that links a doc into the group of the first box that links it,
    so the doc is one row per system (today's and the target's containers may have other ids)."""
    primeiro: dict[tuple[str, str], tuple[str, ...]] = {}
    todos = sorted(((e, k) for k in grupos if k for e in grupos[k]), key=lambda p: (not modelo.e_hoje(p[0]["id"]), p[0]["id"]))
    for e, chave in todos:  # today's box first
        if e["id"] in linked:
            primeiro.setdefault((chave[0], linked[e["id"]]), chave)
    for chave in [k for k in grupos if k]:
        fica = []
        for e in grupos[chave]:
            destino = primeiro.get((chave[0], linked.get(e["id"], "")), chave)
            (fica if destino == chave else grupos[destino]).append(e)
        grupos[chave] = fica


def _tabela_caixas(elements: list[dict], linked: dict[str, str], raiz: Path) -> list[str]:
    linhas: dict[str, list[str]] = {}
    for e in sorted(elements, key=lambda e: docs.peca(e["id"])):
        if e["id"] in linked:
            linhas.setdefault(linked[e["id"]], [])
            titulo = e["title"] or e["id"]
            if titulo not in linhas[linked[e["id"]]]:
                linhas[linked[e["id"]]].append(titulo)
    out = ["| Doc | Boxes | Read when |", "| --- | --- | --- |"]
    for path, titulos in linhas.items():
        fm = _front(raiz, path)
        status = " (esboço)" if fm.get("status") == "esboço" else ""
        out.append(f"| {path}{status} | {_celula(', '.join(titulos))} | {_celula(fm.get('read_when', ''))} |")
    return out


def gerar(m: dict, raiz: Path = REPO) -> str:
    """The text of docs/INDEX.md for the model `m` and the docs under `raiz`."""
    linked = docs.docs_por_elemento(m)
    out = [f"<!-- Generated by `{COMANDO}`: do not edit by hand. -->", "", "# INDEX", "",
           "Routing manifest of docs/, generated from the C4 model (`arquitetura/`) and the front matter "
           "of each doc. A box's doc is the `doc` link of its element; a doc that belongs to no box says "
           "`transversal: true` in its front matter. How to keep it: docs/documentation.md.", "",
           "## Boxes of the model", ""]
    for nivel, titulo, elements in _secoes(m, linked):
        out += ["#" * nivel + f" {titulo}", ""]
        out += [*_tabela_caixas(elements, linked, raiz), ""] if elements else []
    out += ["## Transversal docs", "", "| Doc | Topic | Read when |", "| --- | --- | --- |"]
    for path in _todos(raiz):
        fm = _front(raiz, path)
        if _transversal(raiz, path):
            out.append(f"| {path} | {_celula(fm.get('topic', ''))} | {_celula(fm.get('read_when', ''))} |")
    out += ["", "## Frozen ADRs", "",
            "docs/adr/ is frozen (df-883u): 0001-0003 are history; 0004-0006 hold rules in force until "
            "their content moves to the reference docs (df-cyjt).", "", "| Doc | Status | Read when |", "| --- | --- | --- |"]
    out += [f"| {p} | {ADRS[p][0]} | {_celula(ADRS[p][1])} |" for p in _todos(raiz) if p in ADRS]
    out += ["", "## History (main checkout only)", "",
            "Background only, not current instructions: " + ", ".join(f"`{h}`" for h in HISTORIA)
            + ". They hold real values, so they stay gitignored and exist only in the main checkout.", ""]
    return "\n".join(out)


def fora_do_indice(m: dict, raiz: Path = REPO) -> list[str]:
    """The docs in no part of the INDEX."""
    linked = set(docs.docs_por_elemento(m).values())
    return [p for p in _todos(raiz) if p not in linked and p not in ADRS and not _transversal(raiz, p)]


def conferir(m: dict, raiz: Path = REPO) -> list[str]:
    """The problems, one `error:` line each; empty when the INDEX is the generated one."""
    problems = [f"error: {p} is in no part of the INDEX: link it from a box of arquitetura/, add "
                "`transversal: true` to its front matter, or (an ADR) add it to ADRS" for p in fora_do_indice(m, raiz)]
    indice = raiz / INDICE
    atual = indice.read_text(encoding="utf-8") if indice.is_file() else None
    if atual != gerar(m, raiz):
        problems.append(f"error: {INDICE} is not the generated one: run {COMANDO}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("acao", choices=("gerar", "conferir"))
    acao = parser.parse_args(argv).acao
    m = modelo.load()
    if acao == "gerar":
        (REPO / INDICE).write_text(gerar(m), encoding="utf-8", newline="\n")
        print(f"wrote {INDICE}")
        return 0
    problems = conferir(m)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"{INDICE} is the generated one and lists every doc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
