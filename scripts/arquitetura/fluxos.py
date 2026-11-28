r"""Flows of the C4 model (df-8x0m.23.41): every dynamic view of arquitetura/*.c4 draws one flow of
today, step by step, and names the end-to-end test that exercises it, so a flow drawn and never run
fails here instead of going stale. Each step is an arrow of the model (df-8x0m.23.50), so a flow
cannot draw a link the model lacks.

The view names its test with a link (LikeC4 1.61.1 refuses a metadata block in a view, and a link's
address is a bare URI token, with no spaces): the address is the test file, relative to arquitetura/
where every .c4 lives, and the title is `e2e: ` followed by the test's exact name:

    dynamic view fluxo-lista {
      link ../ninho/telas/e2e/tela.spec.ts 'e2e: na Lista, adiciona um item, recarrega, ...'
      ...
    }

Fails, one `error:` line per problem, when a dynamic view:

1. has no link whose title starts with `e2e`, or one titled without a name after `e2e: `;
2. links an address that is not `../` and a normal repository path (no `.`, `..` or empty
   segment, no `#` or `?`);
3. links a file that is not an end-to-end test: no path segment `e2e` and no `*_e2e.py` name;
4. links a file that does not exist in the repository (a directory, a symlink out of it), or one
   that is not UTF-8 text;
5. links a test name the file does not declare: in Python a `def test_...` (also async), in a
   Playwright spec a `test('...')` call (`test.only`, `.skip`, `.fixme`, `.fail`, `.slow` too;
   a `test.describe` title is not a test, nor a test( in a comment, in a string or after a dot);
   a comment or a string that never closes runs to the end of the text (a quote, to the end of
   the line). There is no JS parser, so the reader has limits. Most only miss a test: a regex
   literal with `//` in it (/https?:\/\//) reads as a comment to the end of the line, and the
   unescape only drops the backslash (and a backslash-newline), so a `\u00e7` in a name reads
   as `u00e7`, not as the character: write the character itself in a name that a flow links.
   A regex literal with a quote or a backtick in it (/'/, /`/) reads as a string start: it can
   miss a test, and can also read the text of a later string as code, so a test( inside that
   string counts (a test invented). Keep such a literal out of an e2e file that a flow names;
6. has no steps, or a step with an end outside today's model (modelo.e_hoje): the tests run today's
   code, so the flow they exercise is today's;
7. has a step that is not an arrow of the model (df-8x0m.23.50): a step a -> b needs an arrow
   from a, or from an element inside a, to b, or to an element inside b, whatever its title. This
   is LikeC4's implied relation: a step between containers is backed by an arrow between their
   components. An arrow only between ancestors does not count (a step between components needs
   the arrow between them), and neither does an arrow the other way: a reply needs its own arrow,
   because the export cannot tell one from a step (`a <- b` in a .c4 and `b -> a` both export as
   b -> a, with the same `dir: back`, which LikeC4 also puts on a plain step whose target came
   earlier in the flow). The fix is to add the arrow to the model or to correct the step.

Reads arquitetura/modelo.json through modelo.load(); the command line takes no argument, so nothing
from it reaches a file read. Exit 0 when clean, 1 with the problems, 2 on an unknown argument.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: arquitetura.modelo

from arquitetura import modelo

REPO = modelo.REPO
MARCA = "e2e"
PREFIXO = f"{MARCA}: "
RAIZ_LINK = "../"  # the .c4 files are in arquitetura/: a relative link starts one folder up
LEGIVEIS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs")
TESTE_PY = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)[ \t]*\(", re.MULTILINE)
# A JS/TS string: each character is one escape or one plain character, so the branches never overlap
# and the match is linear (no backtracking).
_TEXTO_TS = r"'(?:[^'\\\n]|\\[\s\S])*'|\"(?:[^\"\\\n]|\\[\s\S])*\"|`(?:[^`\\]|\\[\s\S])*`"
# The forms that are only skipped also close at the end of the text (a quote also before a newline):
# a token that never closes then takes the rest at once, instead of failing and the scan starting
# over at the next character, which made an unclosed input quadratic.
_PULA_TS = (r"//[^\n]*|/\*(?:[^*]|\*(?!/))*(?:\*/|\Z)"
            r"|'(?:[^'\\\n]|\\[\s\S])*(?:'|(?=\n)|\\?\Z)|\"(?:[^\"\\\n]|\\[\s\S])*(?:\"|(?=\n)|\\?\Z)"
            r"|`(?:[^`\\]|\\[\s\S])*(?:`|\\?\Z)")
# Read left to right, a comment or a string is consumed whole, so a test( inside one never matches;
# the lookbehind refuses a method (/x/.test(...)) or a longer name (contest, $test).
TOKENS_TS = re.compile(r"(?<![.\w$])test(?:\.(?:only|skip|fixme|fail|slow))?\(\s*(?P<nome>" + _TEXTO_TS + r")|"
                       + _PULA_TS)
ESCAPE = re.compile(r"\\([\s\S])")


def _sem_escape(literal: str) -> str:
    """The value of a string literal's body: a backslash and newline (line continuation) go, any other
    backslash goes and keeps the next character (so \\u00e7 reads as u00e7, not as ç)."""
    return ESCAPE.sub(lambda m: "" if m[1] == "\n" else m[1], literal)


def declarados(texto: str, sufixo: str) -> set[str]:
    """The test names a file of that suffix declares (rule 5 of the module docstring)."""
    if sufixo == ".py":
        return set(TESTE_PY.findall(texto))
    return {_sem_escape(m["nome"][1:-1]) for m in TOKENS_TS.finditer(texto) if m["nome"]}


def _caminho(url: str) -> str | None:
    """The repository path of a link address, or None when it is not `../` and a normal path."""
    if not url.startswith(RAIZ_LINK) or any(c in url for c in "#?\\"):
        return None
    resto = url[len(RAIZ_LINK):]
    partes = resto.split("/")
    if any(p in ("", ".", "..") for p in partes):
        return None
    return resto


def _e_e2e(caminho: str) -> bool:
    puro = PurePosixPath(caminho)
    return MARCA in puro.parts or puro.name.endswith(f"_{MARCA}.py")


def _arquivo(repo: Path, caminho: str) -> Path | None:
    """The file at that repository path, or None when there is none inside the repository."""
    alvo = (repo / caminho).resolve()
    if not alvo.is_file() or not alvo.is_relative_to(repo.resolve()):
        return None
    return alvo


def _problema_do_link(repo: Path, link: dict) -> str | None:
    url, titulo = str(link.get("url") or ""), str(link.get("title") or "")
    nome = titulo[len(PREFIXO):].strip() if titulo.startswith(PREFIXO) else ""
    if not nome:
        return f"o link {url!r} tem o título {titulo!r}: o título do link é '{PREFIXO}<nome do teste>'"
    caminho = _caminho(url)
    if caminho is None:
        return (f"o link {url!r}: o endereço começa com {RAIZ_LINK} e segue com o caminho do arquivo no "
                "repositório, sem segmento vazio, '.' ou '..', sem # nem ?")
    return _problema_do_teste(repo, caminho, nome)


def _problema_do_teste(repo: Path, caminho: str, nome: str) -> str | None:
    """Rules 3 to 5 of the module docstring, for one test named by its file and name."""
    if not _e_e2e(caminho):
        return f"{caminho} não é um teste de ponta a ponta (nem pasta {MARCA}/ nem nome *_{MARCA}.py)"
    arquivo = _arquivo(repo, caminho)
    if arquivo is None:
        return f"{caminho} não existe no repositório"
    if arquivo.suffix not in LEGIVEIS:
        return f"{caminho}: só sei ler testes {', '.join(LEGIVEIS)}"
    try:
        texto = arquivo.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"{caminho} não é texto UTF-8"
    if nome not in declarados(texto, arquivo.suffix):
        return f"{caminho} não declara o teste {nome!r}"
    return None


def setas(model: dict) -> set[tuple[str, str]]:
    """The (source, target) of every arrow of the model."""
    return {(r["source"], r["target"]) for r in model.get("relations") or ()}


def _dentro(id_: str, ponta: str) -> bool:
    """Whether the element id_ is ponta itself or one of its descendants."""
    return id_ == ponta or id_.startswith(f"{ponta}.")


def _coberto(origem: str, destino: str, existentes: set[tuple[str, str]]) -> bool:
    """Rule 7 of the module docstring: an arrow from origem (or below it) to destino (or below it)."""
    return any(_dentro(de, origem) and _dentro(para, destino) for de, para in existentes)


def _problema_do_passo(passo: dict, existentes: set[tuple[str, str]]) -> str | None:
    """Rules 6 and 7 of the module docstring, for one step."""
    origem, destino = passo["source"], passo["target"]
    if not (modelo.e_hoje(origem) and modelo.e_hoje(destino)):
        return f"o passo {origem} -> {destino} usa um elemento fora do modelo de hoje"
    if _coberto(origem, destino, existentes):
        return None
    return (f"o passo {origem} -> {destino} {passo.get('title') or ''!r} não é seta do modelo: falta uma "
            f"seta de {origem} (ou de um elemento dentro dele) para {destino} (ou um elemento dentro dele); "
            "acrescente a seta ao modelo ou corrija o passo")


def _problemas_dos_passos(steps: list, existentes: set[tuple[str, str]]) -> list[str]:
    if not steps:
        return ["a vista não tem passos"]
    return [p for p in (_problema_do_passo(s, existentes) for s in steps) if p]


def _problemas_da_vista(repo: Path, vista: dict, existentes: set[tuple[str, str]]) -> list[str]:
    links = [link for link in vista.get("links") or () if str(link.get("title") or "").startswith(MARCA)]
    achados = [] if links else [f"nenhum link '{PREFIXO}<teste>' para o teste de ponta a ponta"]
    achados += [p for p in (_problema_do_link(repo, link) for link in links) if p]
    achados += _problemas_dos_passos(vista.get("steps") or [], existentes)
    return [f"{vista['id']}: {p}" for p in achados]


def dinamicas(model: dict) -> list[dict]:
    return sorted((v for v in model.get("views") or () if v.get("type") == "dynamic"), key=lambda v: v["id"])


def problemas(model: dict, repo: Path | None = None) -> list[str]:
    """Every problem of the model's dynamic views, sorted by view (module docstring)."""
    repo, existentes = repo or REPO, setas(model)
    return [p for vista in dinamicas(model) for p in _problemas_da_vista(repo, vista, existentes)]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    model = modelo.load()
    achados = problemas(model)
    for problema in achados:
        print(f"error: {problema}")
    if achados:
        return 1
    print(f"{len(dinamicas(model))} vistas dinâmicas, cada uma com o seu teste de ponta a ponta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
