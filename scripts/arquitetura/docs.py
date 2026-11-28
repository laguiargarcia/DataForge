"""The doc of every element of the C4 model (df-8x0m.23.39.1): each element of arquitetura/*.c4,
at every level (people, systems, external systems, containers, databases and components, of today
and of the target), links exactly one doc under docs/, and that doc has a front matter.

    python3 scripts/arquitetura/docs.py conferir   # exit 1 with one `error:` line per problem
    python3 scripts/arquitetura/docs.py esbocar    # link and sketch every element without a doc

The link, in the element's body (after its tags), with the title `doc`; the address is relative to
arquitetura/, where every .c4 lives:

    motorHoje = componente 'Motor de execução' {
      link ../docs/subsystems/engine.md 'doc'
      ...

Several elements may share a doc: today's and the target's box of the same piece, or a reference
doc that covers more than one box. `conferir` fails, naming the element, when an element has no
`doc` link or more than one, when the address is not `../docs/<path>.md`, when the doc does not
exist, has no front matter or a front matter without topic, read_when, code or status (`esboço` or
`pronto`), when its code: does not cover every metadata.codigo path of the elements that link it
(a path is covered by itself or by a folder above it; the doc may add paths, and each code: path
must exist), and when a file under docs/caixas/ is linked by no element.

`esbocar` gives every element without a `doc` link the sketch docs/caixas/<id>.md, where <id> is the
element id without the Hoje/Alvo suffix of each segment (casaHoje.dataforgeHoje.motorHoje ->
casa.dataforge.motor, so today's and the target's box of the same piece share it), and writes the
link line into the element's definition in arquitetura/*.c4 (or prints it, when the definition is
not found exactly once). Then it writes every linked doc that does not exist yet as a sketch, from
the model only (titles, kind, descriptions, technology, codigo): status esboço. It never rewrites
an existing doc or link, so a second run changes nothing. It reads arquitetura/modelo.json: after
it changes a .c4, run `python3 scripts/arquitetura/modelo.py gerar`.

Front matter: the first line of the file is `---`, and the block ends at the next `---`. It is a
flat subset of YAML that this module reads by hand, so it runs under `python3 -S` (the bead hook):
only `key: value` lines (a plain value, a "double-quoted" JSON string, a 'single-quoted' string or
an inline list `[a, b]`) and `key:` followed by `- item` lines (a list); nothing nested.

    ---
    topic: Engine
    read_when: When working on DAG execution, task running, or job orchestration
    code:
      - dataforge/engine.py
      - dataforge/dag.py
    status: pronto
    ---

API, for the other scripts (put scripts/ on sys.path, as scripts/beads/check.py does). Only the
standard library:

    from arquitetura import docs
    docs.docs_por_elemento()      # {element id: doc path relative to the repo ("docs/x.md")}, from
                                  # modelo.load(); or docs_por_elemento(m) for a model dict. Only
                                  # the elements with a `doc` link of the form ../docs/<path>.md
    docs.ler_front_matter(path)   # {key: value} of a doc's front matter: topic, read_when (str),
                                  # code (list of str), status ('esboço' | 'pronto'), plus any other
                                  # key as written. {} when the file has no front matter;
                                  # ValueError on a line outside the subset above
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: arquitetura.modelo

from arquitetura import modelo

REPO = modelo.REPO
FONTE = modelo.FONTE
TITULO = "doc"
PREFIXO = "../"  # a link's address is relative to arquitetura/
CAIXAS = "docs/caixas"
STATUS = ("esboço", "pronto")
CHAVES = ("topic", "read_when", "code", "status")
_LINHA = re.compile(r"([A-Za-z_][\w-]*):(?:\s+(.*))?")
_ITEM = re.compile(r"\s*-\s+(.*)")
_PLANO = re.compile(r"[\w./-]+")
_ASPAS = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_LINK = re.compile(rf"\s*link\s+\S+\s+'{TITULO}'\s*")


def _links_doc(element: dict) -> list[str]:
    return [link["url"] for link in element.get("links") or [] if link.get("title") == TITULO]


def _caminho(url: str) -> str | None:
    """docs/<path>.md of a `doc` link address, or None when it has another form."""
    if not url.startswith(PREFIXO):
        return None
    path = url[len(PREFIXO):]
    parts = path.split("/")
    if parts[0] != "docs" or len(parts) == 1 or not path.endswith(".md") or any(p in ("", ".", "..") for p in parts):
        return None
    return path


def docs_por_elemento(m: dict | None = None) -> dict[str, str]:
    """{element id: doc path relative to the repo} for each element with one well-formed `doc` link."""
    m = modelo.load() if m is None else m
    out = {}
    for element in m["elements"]:
        urls = _links_doc(element)
        if len(urls) == 1 and (path := _caminho(urls[0])):
            out[element["id"]] = path
    return out


def _valor(text: str, where: str):
    text = text.strip()
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"{where}: bad double-quoted string {text!r}") from None
        if not isinstance(value, str):
            raise ValueError(f"{where}: bad double-quoted string {text!r}")
        return value
    if text.startswith("'"):
        if text == "'" or not text.endswith("'"):
            raise ValueError(f"{where}: bad single-quoted string {text!r}")
        return text[1:-1].replace("''", "'")
    return text


def _lista(text: str, where: str) -> list[str]:
    inner = text.strip()[1:-1].strip()
    return [_valor(item, where) for item in inner.split(",")] if inner else []


def ler_front_matter(path: Path | str) -> dict:
    """The front matter of the doc at `path` (see the module docstring)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict = {}
    lista: list | None = None
    for number, line in enumerate(lines[1:], start=2):
        where = f"{path}:{number}"
        if line.strip() == "---":
            return out
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _ITEM.fullmatch(line)
        if item and lista is not None:
            lista.append(_valor(item.group(1), where))
            continue
        match = _LINHA.fullmatch(line)
        if not match:
            raise ValueError(f"{where}: not a flat `key: value` or `- item` line: {line!r}")
        key, value = match.group(1), (match.group(2) or "").strip()
        lista = None
        if not value:
            out[key] = lista = []
        elif value.startswith("[") and value.endswith("]"):
            out[key] = _lista(value, where)
        else:
            out[key] = _valor(value, where)
    raise ValueError(f"{path}: the front matter opened on line 1 never closes with ---")


def _cobre(code: list[str], path: str) -> bool:
    return any(path == c or path.startswith(c.rstrip("/") + "/") for c in code)


def _codigo(element: dict) -> list[str]:
    value = (element.get("metadata") or {}).get("codigo") or []
    return [value] if isinstance(value, str) else list(value)


def _problemas_doc(path: str, ids: list[str], por_id: dict, raiz: Path) -> list[str]:
    quem = ", ".join(ids)
    file = raiz / path
    if not file.is_file():
        return [f"error: {quem}: links {path}, which does not exist "
                "(python3 scripts/arquitetura/docs.py esbocar writes a sketch)"]
    try:
        fm = ler_front_matter(file)
    except ValueError as error:
        return [f"error: {quem}: the front matter of {path} is broken: {error}"]
    if not fm:
        return [f"error: {quem}: {path} has no front matter (--- on its first line; docs/documentation.md)"]
    problems = []
    for key in ("topic", "read_when"):
        if not isinstance(fm.get(key), str) or not fm[key].strip():
            problems.append(f"error: {quem}: the front matter of {path} has no {key}")
    code = fm.get("code")
    if not isinstance(code, list):
        problems.append(f"error: {quem}: the front matter of {path} has no code: list")
        code = []
    if fm.get("status") not in STATUS:
        problems.append(f"error: {quem}: the front matter of {path} has status {fm.get('status', '(none)')}, "
                        f"not one of {', '.join(STATUS)}")
    problems += [f"error: {quem}: code: of {path} names {c}, which does not exist"
                 for c in code if not (raiz / c).exists()]
    problems += [f"error: {eid}: code: of {path} does not cover its codigo path {p}"
                 for eid in ids for p in _codigo(por_id[eid]) if not _cobre(code, p)]
    return problems


def conferir(m: dict, raiz: Path = REPO) -> list[str]:
    """The problems of the docs of `m` (module docstring), one `error:` line each; empty when fine."""
    por_id = {e["id"]: e for e in m["elements"]}
    problems = []
    for eid, element in sorted(por_id.items()):
        urls = _links_doc(element)
        if not urls:
            problems.append(f"error: {eid} has no doc link: add link ../{_padrao(eid)} '{TITULO}' to it "
                            "(python3 scripts/arquitetura/docs.py esbocar does it)")
        elif len(urls) > 1:
            problems.append(f"error: {eid} has {len(urls)} doc links ({', '.join(urls)}): keep one")
        elif _caminho(urls[0]) is None:
            problems.append(f"error: {eid}: doc link {urls[0]!r} is not ../docs/<path>.md")
    linked = docs_por_elemento(m)
    for path, ids in sorted(_agrupar(linked).items()):
        problems += _problemas_doc(path, ids, por_id, raiz)
    caixas = raiz / CAIXAS
    if caixas.is_dir():
        problems += [f"error: {f.relative_to(raiz).as_posix()} is linked by no element of the model: remove it"
                     for f in sorted(caixas.rglob("*.md")) if f.relative_to(raiz).as_posix() not in linked.values()]
    return problems


def _agrupar(linked: dict[str, str]) -> dict[str, list[str]]:
    grupos = defaultdict(list)
    for eid, path in sorted(linked.items()):
        grupos[path].append(eid)
    return grupos


def _sem_sufixo(segment: str) -> str:
    for suffix in (modelo.HOJE, modelo.ALVO):
        if segment.endswith(suffix) and len(segment) > len(suffix):
            return segment[: -len(suffix)]
    return segment


def _padrao(eid: str) -> str:
    """docs/caixas/<id without Hoje/Alvo>.md, the sketch of an element without a doc link."""
    return f"{CAIXAS}/{'.'.join(_sem_sufixo(s) for s in eid.split('.'))}.md"


def _sem_aspas(line: str) -> str:
    return _ASPAS.sub("", line.split("//", 1)[0])


def _definicoes(name: str, kind: str, fonte: Path) -> list[tuple[Path, int]]:
    pattern = re.compile(rf"\s*{re.escape(name)}\s*=\s*{re.escape(kind)}\b.*\{{\s*")
    return [(f, i) for f in sorted(fonte.glob("*.c4"))
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines()) if pattern.fullmatch(line)]


def _ligar(element: dict, path: str, fonte: Path) -> str:
    """Write `link ../<path> 'doc'` into the element's definition; the message saying what was done."""
    eid = element["id"]
    line_link = f"link {PREFIXO}{path} '{TITULO}'"
    found = _definicoes(eid.rsplit(".", 1)[-1], element["kind"], fonte)
    if len(found) != 1:
        return f"{eid}: add `{line_link}` to its body in arquitetura/ (its definition was found {len(found)} times)"
    file, index = found[0]
    lines = file.read_text(encoding="utf-8").splitlines(keepends=True)
    depth, end = 0, len(lines)
    for i in range(index, len(lines)):
        if i > index and depth == 1 and _LINK.fullmatch(lines[i].rstrip("\n")):
            return f"{eid}: already linked in {file.name}; run python3 scripts/arquitetura/modelo.py gerar"
        clean = _sem_aspas(lines[i])
        depth += clean.count("{") - clean.count("}")
        if depth == 0:
            end = i
            break
    at = index + 1
    while at < end and lines[at].lstrip().startswith("#"):
        at += 1
    indent = re.match(r"\s*", lines[index]).group(0) + "  "
    lines.insert(at, f"{indent}{line_link}\n")
    file.write_text("".join(lines), encoding="utf-8")
    return f"{eid}: linked {path} in {file.name}"


def _escalar(text: str) -> str:
    return text if _PLANO.fullmatch(text) else json.dumps(text, ensure_ascii=False)


def _esboco(elements: list[dict]) -> str:
    titles = list(dict.fromkeys(e["title"] or e["id"] for e in elements))
    code = sorted({p for e in elements for p in _codigo(e)})
    code = [p for p in code if not any(c != p and _cobre([c], p) for c in code)]
    ids = ", ".join(e["id"] for e in elements)
    lines = ["---", f"topic: {json.dumps(' / '.join(titles), ensure_ascii=False)}",
             f"read_when: {json.dumps(f'Working on the C4 box {ids} or the code it covers', ensure_ascii=False)}"]
    lines += ["code:", *[f"  - {_escalar(p)}" for p in code]] if code else ["code: []"]
    lines += ["status: esboço", "---", "", f"# {' / '.join(titles)}", "",
              "Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 "
              "model (`arquitetura/`). Write the real doc over it and set `status: pronto`.", "",
              "| Element | Kind | Technology |", "| --- | --- | --- |"]
    lines += [f"| `{e['id']}` | {e['kind']} | {e.get('technology') or ''} |" for e in elements]
    for e in elements:
        if e.get("description"):
            lines += ["", f"`{e['id']}`: {e['description']}"]
    if code:
        lines += ["", "Code: " + ", ".join(f"`{p}`" for p in code) + "."]
    return "\n".join(lines) + "\n"


def esbocar(m: dict, raiz: Path = REPO, fonte: Path = FONTE) -> list[str]:
    """Link every element without a `doc` link to its sketch path, then write every linked doc that
    does not exist as a sketch. The messages, one line per thing done."""
    por_id = {e["id"]: e for e in m["elements"]}
    messages = []
    linked = docs_por_elemento(m)
    for eid, element in sorted(por_id.items()):
        if not _links_doc(element):
            linked[eid] = _padrao(eid)
            messages.append(_ligar(element, linked[eid], fonte))
    for path, ids in sorted(_agrupar(linked).items()):
        file = raiz / path
        if not file.exists():
            file.parent.mkdir(parents=True, exist_ok=True)
            # today's box first: the sketch describes what exists, then what the target changes
            elements = sorted((por_id[i] for i in ids), key=lambda e: (not modelo.e_hoje(e["id"]), e["id"]))
            file.write_text(_esboco(elements), encoding="utf-8")
            messages.append(f"{path}: sketch written for {', '.join(ids)}")
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("acao", choices=("conferir", "esbocar"))
    acao = parser.parse_args(argv).acao
    m = modelo.load()
    if acao == "esbocar":
        messages = esbocar(m)
        print("\n".join(messages) if messages else "every element links a doc that exists")
        if any(" linked " in msg or "add `link" in msg for msg in messages):
            print("the .c4 changed: run python3 scripts/arquitetura/modelo.py gerar, then conferir")
        return 0
    problems = conferir(m)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"every element of {modelo.SAIDA.relative_to(REPO)} links a doc with front matter")
    return 0


if __name__ == "__main__":
    sys.exit(main())
