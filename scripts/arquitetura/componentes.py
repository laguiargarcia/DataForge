"""The component model (C4 level 3) of arquitetura/*.c4 checked against the code (df-8x0m.23.47.2, .9).

    uv run --no-project python scripts/arquitetura/componentes.py   # exit 1 with one `error:` line per problem

It reads arquitetura/modelo.json (scripts/arquitetura/modelo.py) and the files git tracks (`git
ls-files`, never the disk: a local .venv, __pycache__ or docs/ is not in the CI checkout). It prints
the warnings, then the errors, each sorted by file and line, then a summary line, and exits 1
when there is an error. Warnings never fail: they list what a static read cannot check, for the
reviewer of the drawing. Standard library only. It parses the code with the ast of the Python that
runs it, so it must be the repo's (.python-version): the CI job `arquitetura` runs it with `uv run
--no-project python`, since an older python3 refuses newer syntax. Run by a Python older than the
major.minor of .python-version, it says so and exits 2 before reading anything.

Every element of kind `componente` points at its code with `metadata { codigo [...] }`, as do the
containers with code, and an arrow may list the files where its call is. Paths are from the root of
the repo, a file or a folder. LikeC4 exports a list of one item as a string; both are read.

The model holds two drawings, the hoje and the alvo. The system of an element is the first part of
its id (casaHoje, casaAlvo, donoHoje); the hoje is every system whose name ends in "Hoje" (mother
constraint 2 of df-8x0m.23.47). Rules (2) to (5) are per system, so the same path in a box of the
hoje and in one of the alvo is no conflict. Rules (3), (4) and (7) run on the hoje only: the alvo
draws code that does not exist yet.

(1) Every codigo path, of any element, arrow or deployment item, in any system, is in normal form
    (relative, '/' between parts, no '.', '..' or empty part) and is a tracked file or a folder
    with tracked files.
(2) The owner of a file is the element of its system whose codigo path is the longest one that is
    the file or a folder above it. Containers nest by path but not in the model (pipeline holds
    apps/financas, which holds its backend): a file under a nested container is that container's.
(3) A static import from a code file to a code file of a different owner needs an arrow in the
    model whose source is exactly the importing file's owner and whose target is exactly the
    imported file's owner, with any title or kind. An arrow between their parents or their
    children does not count. Not checked: a pair where one owner is an ancestor of the other by
    id (LikeC4 refuses an arrow between parent and child; rule (4) catches a file the parent owns),
    and a pair where neither owner is a componente (an import between two containers without
    components). When components are added to a container later, the arrows that enter it point
    at the container: this rule asks for them to be pointed at the component that owns the file.
(4) An owner that is not a componente but has a componente inside it (a container with
    components) owns no code file: one of its components (or of a nested container) does.
(5) One path in the codigo of two elements of the same system is an error: the owner would be
    ambiguous.
(6) Every codigo path of a componente, hoje or alvo, is inside (equal to or under) a codigo path of
    its nearest ancestor by id that has codigo; a componente with codigo and no such ancestor is an
    error. Without it a component could take the files of another container and pass rule (4).
(7) An import that asks for an arrow in rule (3), when the arrow is there, has the importing file
    in the codigo of an arrow of that pair (any of them, if several titles share it), as the file or
    a folder above it. One error per file and pair, at its first import. A codigo path of an arrow
    that holds no import is not checked: it may hold the HTTP or CLI call of the same arrow.

Code files: suffixes .py .ts .tsx .js .jsx .mjs .cjs .sh .ps1. Out of scope, so they need no owner,
their imports are not read and an import of them is not checked: anything under a folder named
dist, node_modules, tests, test, __tests__ or e2e (built bundles, dependencies and tests are not
the running system); test_*.py, *_test.py, conftest.py, *.test.*, *.spec.*; and the tooling
configs *.config.ts, *.config.js, *.config.mjs, *.config.cjs (vite, playwright).

Python imports are read with ast (import and from, anywhere in the file, inside functions too) and
resolved to tracked files, trying these bases in order: the sys.path.insert entries of the file,
its own folder when it holds no __init__.py (Python puts a script's folder on sys.path; a module of
a package does not get its own, so `import logging` there is the standard library, never a sibling
logging.py), the repo root, and the sys.path.append entries. A sys.path entry is evaluated
statically when it is built from Path(__file__) with .parent, .parents[n], .resolve(),
.absolute(), / "literal" and str(...), or from a module-level name assigned such an expression;
any other entry is a warning. `import a.b.c` leads to a/b/c.py or a/b/c/__init__.py; `from a.b
import n` to a/b/n when it is a module, else to a/b; a relative import resolves from the file's own
package. A name no base resolves (yaml, polars) is not code of the repo.

An import also imports, before the module, the __init__.py of each package between the base it
resolved from and the module (`from dataforge import delta_pool` runs dataforge/__init__.py first):
each one counts as an import of that line, checked as in (3) and (7) (df-8x0m.23.47.9). Python runs
that code in the importing process, so the dependency is real. Not counted: the packages that hold
the importing file, which ran before the importing file itself, and the packages above the base
(with app/b on sys.path, `import y` is the top-level y). An exec of a file runs that file alone.

Loads: exec(<path expression above>.read_text(...)) (the workspace scripts' functions.py) and
importlib.import_module("<literal name>") that resolves are imports, checked as in (3). Any other
exec, import_module, __import__, spec_from_file_location, run_path or run_module is a warning.
A call that starts a process is a warning, since an arrow for a CLI call needs a human to check it:
subprocess.run, Popen, call, check_call and check_output, asyncio.create_subprocess_exec and
create_subprocess_shell, os.exec* and os.spawn* (aliases of the module or the function too), except
when the program is a literal that is neither python, dataforge nor a shell (sh, bash, cmd,
powershell, pwsh); os.system and os.popen always. In an owned .sh or .ps1, every line that names
dataforge or python as a word, other than a '#' comment, is a warning. Imports under `if
TYPE_CHECKING:`, like TS `import type`, are imports: a compile-time dependency needs its arrow too.
A Python, TS or JS file is read as UTF-8 (a BOM is fine); one that is not is an error. A shell
script is read as UTF-8 or, with its BOM, UTF-16; one that is neither is a warning.

TS and JS imports are read by pattern, with the comments taken out first: import/export ... from
'...' (several lines and `import type` too), import '...', and import('...'); an import() of
anything but a string literal is a warning. require() and `import x = require()` are not read: the
front-ends use neither. Only relative specifiers ('./', '../') are code of the repo: the front-ends
have no path aliases. A specifier resolves to the path itself, the path plus a code suffix, or its
index file; a target that is not a code file (css, svg) is not checked; a relative specifier that
resolves to no tracked file is a warning.

An import of a tracked code file that no element owns is a warning, from Python or TS alike.
"""

from __future__ import annotations

import ast
import codecs
import posixpath
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/: arquitetura.modelo

from arquitetura import modelo

REPO = Path(__file__).resolve().parents[2]
COMPONENTE = "componente"
CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".ps1")
TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
OUT_FOLDERS = frozenset({"dist", "node_modules", "tests", "test", "__tests__", "e2e"})
OUT_CONFIGS = (".config.ts", ".config.js", ".config.mjs", ".config.cjs")
SUBPROCESS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
ASYNCIO = frozenset({"create_subprocess_exec", "create_subprocess_shell"})
ALWAYS = frozenset({"system", "popen"})  # os.system and os.popen run a shell command line
PROCESS_MODULES = ("subprocess", "os", "asyncio")
SHELL_CALL = re.compile(r"(?i)\b(?:dataforge|python)\d*(?:\.\d+)?\b")
DYNAMIC = frozenset({"__import__", "spec_from_file_location", "run_path", "run_module"})
SHELLS = frozenset({"sh", "bash", "cmd", "powershell", "pwsh"})  # a program that runs any other one
SNIPPET = 120
POSICAO = re.compile(r"^([^:]+):(?:(\d+):)?")
# import/export ... from '...' is read in steps, each pattern linear: the keyword, then the run of
# characters an import clause is made of, which must end in `from` and spaces, then the quoted
# specifier right after the run (TS_FROM_* and _ts_from).
TS_FROM_KEYWORD = re.compile(r"\b(?:import|export)\s")
TS_FROM_CLAUSE = re.compile(r"[\w\s{},*$]*")
TS_FROM_WORD = re.compile(r"\bfrom")
TS_SPECIFIER = re.compile(r"""(['"])([^'"\n]+)\1""")
TS_IMPORTS = (
    re.compile(r"""\bimport\s*(['"])([^'"\n]+)\1"""),
    re.compile(r"""\bimport\s*\(\s*(['"])([^'"\n]+)\1\s*\)"""),
)
TS_DYNAMIC = re.compile(r"\bimport\s*\(")
# A string (kept) or a comment (replaced by its newlines), so a quote inside a comment, or '//' in a
# string, does not throw the patterns off. TS_TOKEN_START finds where the next one may start, and
# the pattern of that start reads it whole.
TS_TOKEN_START = re.compile(r"""['"`]|//|/\*""")
TS_TOKEN = {
    "'": re.compile(r"'(?:\\.|[^'\\\n])*'", re.DOTALL),
    '"': re.compile(r'"(?:\\.|[^"\\\n])*"', re.DOTALL),
    "`": re.compile(r"`(?:\\.|[^`\\])*`", re.DOTALL),
    "//": re.compile(r"//[^\n]*"),
    "/*": re.compile(r"/\*.*?\*/", re.DOTALL),
}


@dataclass
class Resultado:
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    donos: dict[str, str] = field(default_factory=dict)  # code file of the hoje -> its owner
    # (file, line, imported file, None) or, for a package's __init__.py the import runs first,
    # (file, line, that __init__.py, the imported file)
    imports: list[tuple[str, int, str, str | None]] = field(default_factory=list)


@dataclass
class Repo:
    raiz: Path
    arquivos: frozenset[str]
    pastas: frozenset[str]

    @classmethod
    def de(cls, raiz: Path, arquivos) -> Repo:
        arquivos = frozenset(arquivos)
        pastas = set()
        for arquivo in arquivos:
            pasta = posixpath.dirname(arquivo)
            while pasta and pasta not in pastas:
                pastas.add(pasta)
                pasta = posixpath.dirname(pasta)
        return cls(raiz, arquivos, frozenset(pastas))


def arquivos_do_repo(raiz: Path) -> list[str]:
    """The files git tracks under `raiz`, as posix paths, minus those deleted from the disk."""
    saida = subprocess.run(["git", "-C", str(raiz), "ls-files", "-z"], capture_output=True, check=True).stdout
    return sorted(f for f in saida.decode("utf-8").split("\0") if f and (raiz / f).is_file())


def codigo(item: dict) -> list:
    valor = (item.get("metadata") or {}).get("codigo")
    if valor is None:
        return []
    return [valor] if isinstance(valor, str) else list(valor)


def _fora(nome: str) -> bool:
    """A test or a tooling config, by its file name."""
    return ((nome.startswith("test_") and nome.endswith(".py")) or nome.endswith("_test.py")
            or nome == "conftest.py" or ".test." in nome or ".spec." in nome or nome.endswith(OUT_CONFIGS))


def e_codigo(arquivo: str) -> bool:
    partes = arquivo.split("/")
    return (arquivo.endswith(CODE_SUFFIXES) and not OUT_FOLDERS.intersection(partes[:-1])
            and not _fora(partes[-1]))


def _dentro(caminho: str, pasta: str) -> bool:
    """`caminho` is the path `pasta` or lies under it."""
    return caminho == pasta or caminho.startswith(pasta + "/")


def _normal(caminho: str) -> str | None:
    limpo = posixpath.normpath(caminho.strip("/")) if caminho.strip("/") else "."
    return None if limpo in (".", "..") or limpo.startswith("../") else limpo


def _itens(modelo_: dict):
    """(label, codigo) of every element, arrow and deployment item."""
    implantacao = modelo_.get("deployment") or {}
    for chave in ("elements", "relations"):
        for item in modelo_.get(chave) or []:
            yield item["id"], codigo(item)
    for chave in ("nodes", "instances", "relations"):
        for item in implantacao.get(chave) or []:
            yield item["id"], codigo(item)


def checar_caminhos(modelo_: dict, repo: Repo) -> list[str]:
    """Rule (1)."""
    erros = []
    for label, caminhos in _itens(modelo_):
        for caminho in caminhos:
            normal = _normal(caminho)
            if normal != caminho:
                erros.append(f"{label}: codigo {caminho!r} is not a repo path in normal form "
                             f"(normal form: {repr(normal) if normal else 'none'})")
            elif caminho not in repo.arquivos and caminho not in repo.pastas:
                erros.append(f"{label}: codigo {caminho!r} is not a tracked file or a folder with tracked files")
    return erros


def checar_repetidos(elementos: list[dict]) -> list[str]:
    """Rule (5)."""
    por_caminho: dict[tuple[str, str], set[str]] = {}
    for elemento in elementos:
        for caminho in codigo(elemento):
            por_caminho.setdefault((modelo.sistema(elemento["id"]), caminho), set()).add(elemento["id"])
    return [f"{caminho!r} is in the codigo of {' and '.join(sorted(ids))}: give it one owner"
            for (_, caminho), ids in sorted(por_caminho.items()) if len(ids) > 1]


def donos_do_hoje(elementos: list[dict], repo: Repo) -> dict[str, str]:
    """Rule (2): each code file of the hoje -> the element of longest codigo path that holds it."""
    caminhos = sorted((c, e["id"]) for e in elementos if modelo.e_hoje(e["id"]) for c in codigo(e))
    donos = {}
    for arquivo in sorted(repo.arquivos):
        if not e_codigo(arquivo):
            continue
        achados = [(len(c), dono) for c, dono in caminhos if _dentro(arquivo, c)]
        if achados:
            donos[arquivo] = min(achados, key=lambda achado: (-achado[0], achado[1]))[1]
    return donos


def _ancestral_com_codigo(id_: str, por_id: dict[str, list]) -> str | None:
    """The nearest ancestor of `id_`, by id, that has codigo."""
    partes = id_.split(".")
    return next((pai for fim in range(len(partes) - 1, 0, -1)
                 if por_id.get(pai := ".".join(partes[:fim]))), None)


def checar_dentro(elementos: list[dict]) -> list[str]:
    """Rule (6)."""
    por_id = {e["id"]: codigo(e) for e in elementos}
    erros = []
    for elemento in elementos:
        caminhos = codigo(elemento)
        if elemento["kind"] != COMPONENTE or not caminhos:
            continue
        pai = _ancestral_com_codigo(elemento["id"], por_id)
        if pai is None:
            erros.append(f"{elemento['id']}: has codigo but no ancestor of it has, so its code is in no container's")
            continue
        erros += [f"{elemento['id']}: codigo {caminho!r} is not inside the codigo of its ancestor {pai} "
                  f"({', '.join(map(repr, por_id[pai]))})"
                  for caminho in caminhos if not any(_dentro(caminho, p) for p in por_id[pai])]
    return erros


def checar_componentes(donos: dict[str, str], elementos: list[dict]) -> list[str]:
    """Rule (4)."""
    tipos = {e["id"]: e["kind"] for e in elementos}
    componentes = [id_ for id_, tipo in tipos.items() if tipo == COMPONENTE]
    return [f"{arquivo}: its owner {dono} has components, so one of them must own it"
            for arquivo, dono in donos.items()
            if tipos.get(dono) != COMPONENTE and any(c.startswith(dono + ".") for c in componentes)]


def _parente(a: str, b: str) -> bool:
    return a.startswith(b + ".") or b.startswith(a + ".")


def _par(resultado: Resultado, tipos: dict[str, str], arquivo: str, linha: int, alvo: str) -> tuple[str, str] | None:
    """(source, target) of the arrow rule (3) asks for an import, or None; an import of a code file
    no element owns is a warning."""
    de, para = resultado.donos[arquivo], resultado.donos.get(alvo)
    if para is None and e_codigo(alvo):
        resultado.avisos.append(f"{arquivo}:{linha}: imports {alvo}, which no element owns")
    if para is None or de == para or _parente(de, para) or COMPONENTE not in (tipos.get(de), tipos.get(para)):
        return None
    return de, para


def _falta(par: tuple[str, str] | None, setas: dict[tuple[str, str], set[str]], arquivo: str,
           linha: int) -> tuple[str, int] | None:
    """What the model lacks for an import in `arquivo` at `linha` that asks for the arrow `par`:
    ("seta", linha), ("codigo", 0) when no arrow of the pair lists the file, or None."""
    if par is None:
        return None
    if par not in setas:
        return "seta", linha
    return None if any(_dentro(arquivo, c) for c in setas[par]) else ("codigo", 0)


def checar_setas(resultado: Resultado, elementos: list[dict], relacoes: list[dict]) -> list[str]:
    """Rules (3) and (7)."""
    tipos = {e["id"]: e["kind"] for e in elementos}
    setas: dict[tuple[str, str], set[str]] = {}
    for relacao in relacoes:
        setas.setdefault((relacao["source"], relacao["target"]), set()).update(codigo(relacao))
    erros, vistos = [], set()
    # in file and line order, the imported module before the packages it runs: one error per line
    # and pair for a missing arrow, one per file and pair for a missing codigo
    for arquivo, linha, alvo, modulo_ in sorted(resultado.imports, key=lambda i: (i[0], i[1], i[3] is not None, i[2])):
        par = _par(resultado, tipos, arquivo, linha, alvo)
        falta = _falta(par, setas, arquivo, linha)
        if falta is None or (arquivo, par, falta) in vistos:
            continue
        vistos.add((arquivo, par, falta))
        texto = f"{arquivo}:{linha}: imports {alvo if modulo_ is None else f'{modulo_}, whose package runs {alvo}'}"
        erros.append(f"{texto}, so the model needs an arrow {par[0]} -> {par[1]}" if falta[0] == "seta"
                     else f"{texto}, so the codigo of an arrow {par[0]} -> {par[1]} must list {arquivo}")
    return erros


# Python


def _trecho(fonte: str, no: ast.AST) -> str:
    texto = " ".join((ast.get_source_segment(fonte, no) or "").split())
    return texto if len(texto) <= SNIPPET else texto[:SNIPPET] + "..."


def _juntar(*partes: str) -> str:
    return "/".join(p for p in partes if p)


def _nome(funcao: ast.expr) -> str | None:
    if isinstance(funcao, ast.Name):
        return funcao.id
    return funcao.attr if isinstance(funcao, ast.Attribute) else None


def _caminho(no: ast.expr, arquivo: tuple[str, ...], nomes: dict) -> tuple[str, ...] | None:
    """The repo path a Path(__file__) expression stands for (() is the root), or None."""
    if isinstance(no, ast.Name):
        return arquivo if no.id == "__file__" else nomes.get(no.id)
    if isinstance(no, ast.Call) and not no.keywords:
        return _caminho_chamada(no, arquivo, nomes)
    if isinstance(no, ast.Attribute) and no.attr == "parent":
        base = _caminho(no.value, arquivo, nomes)
        return base[:-1] if base else None
    if isinstance(no, ast.Subscript):
        return _caminho_parents(no, arquivo, nomes)
    if isinstance(no, ast.BinOp):
        return _caminho_barra(no, arquivo, nomes)
    return None


def _caminho_parents(no: ast.Subscript, arquivo: tuple[str, ...], nomes: dict) -> tuple[str, ...] | None:
    """x.parents[n] is x without its last n + 1 parts."""
    if not (isinstance(no.value, ast.Attribute) and no.value.attr == "parents"
            and isinstance(no.slice, ast.Constant) and isinstance(no.slice.value, int)):
        return None
    base = _caminho(no.value.value, arquivo, nomes)
    fim = len(base) - 1 - no.slice.value if base is not None else -1
    return base[:fim] if fim >= 0 else None


def _caminho_barra(no: ast.BinOp, arquivo: tuple[str, ...], nomes: dict) -> tuple[str, ...] | None:
    """x / "literal" is x joined with the literal, in normal form."""
    if not (isinstance(no.op, ast.Div) and isinstance(no.right, ast.Constant) and isinstance(no.right.value, str)):
        return None
    base = _caminho(no.left, arquivo, nomes)
    junto = _normal(_juntar(*base, no.right.value)) if base is not None else None
    return tuple(junto.split("/")) if junto else None


def _caminho_chamada(no: ast.Call, arquivo: tuple[str, ...], nomes: dict) -> tuple[str, ...] | None:
    """Path(x) and str(x) are x; x.resolve() and x.absolute() are x."""
    nome = _nome(no.func)
    if nome in ("Path", "str") and len(no.args) == 1:
        return _caminho(no.args[0], arquivo, nomes)
    if nome in ("resolve", "absolute") and not no.args and isinstance(no.func, ast.Attribute):
        return _caminho(no.func.value, arquivo, nomes)
    return None


def _nomes(arvore: ast.Module, arquivo: tuple[str, ...]) -> dict:
    nomes: dict = {}
    for no in arvore.body:
        if isinstance(no, ast.Assign) and len(no.targets) == 1 and isinstance(no.targets[0], ast.Name):
            valor = _caminho(no.value, arquivo, nomes)
            if valor is None:
                nomes.pop(no.targets[0].id, None)
            else:
                nomes[no.targets[0].id] = valor
    return nomes


def _processos(arvore: ast.Module) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """The local names of subprocess, os and asyncio ({alias: module}) and of their functions
    ({alias: (module, function)}), aliases included."""
    modulos = {m: m for m in PROCESS_MODULES}
    funcoes = {}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            modulos |= {a.asname or a.name: a.name for a in no.names if a.name in PROCESS_MODULES}
        elif isinstance(no, ast.ImportFrom) and no.module in PROCESS_MODULES and not no.level:
            funcoes |= {a.asname or a.name: (no.module, a.name) for a in no.names}
    return modulos, funcoes


@dataclass
class _Leitor:
    """The imports, loads and subprocess calls of one Python file."""
    repo: Repo
    arquivo: str
    fonte: str
    resultado: Resultado
    bases: list[str] = field(default_factory=list)
    lidos: set = field(default_factory=set)
    processos: tuple = ({}, {})

    def modulo(self, base: str, partes: list[str]) -> str | None:
        alvo = _juntar(base, *partes)
        if not alvo:
            return None
        for candidato in (alvo + ".py", _juntar(alvo, "__init__.py")):
            if candidato in self.repo.arquivos:
                return candidato
        return None

    def achar(self, base: str, partes: list[str]) -> tuple[str, str] | None:
        """(base, file) of the module `partes` under `base`, or None."""
        achado = self.modulo(base, partes)
        return (base, achado) if achado else None

    def import_(self, partes: list[str]) -> tuple[str, str] | None:
        return next((achado for base in self.bases if (achado := self.achar(base, partes))), None)

    def from_(self, no: ast.ImportFrom, nome: str) -> tuple[str, str] | None:
        partes = no.module.split(".") if no.module else []
        bases = self.bases
        if no.level:
            pacote = self.arquivo.split("/")[:-1]
            if no.level - 1 > len(pacote):
                return None
            bases = ["/".join(pacote[:len(pacote) - (no.level - 1)])]
        for base in bases:
            achado = (self.achar(base, [*partes, nome]) if nome != "*" else None) or self.achar(base, partes)
            if achado:
                return achado
        return None

    def ligar(self, linha: int, achado: tuple[str, str] | None) -> None:
        """An import of the file found from its base, and of each package __init__.py it runs first."""
        if achado is None:
            return
        base, alvo = achado
        for pacote, modulo_ in [(alvo, None), *((p, alvo) for p in self.pacotes(base, alvo))]:
            if (linha, pacote) not in self.lidos:
                self.lidos.add((linha, pacote))
                self.resultado.imports.append((self.arquivo, linha, pacote, modulo_))

    def pacotes(self, base: str, alvo: str) -> list[str]:
        """The __init__.py of each package between `base` and `alvo`, which Python runs before `alvo`;
        not those of the importing file's own packages, which ran before the importing file."""
        pacotes, pasta = [], posixpath.dirname(alvo)
        while pasta and pasta != base:
            inicio = _juntar(pasta, "__init__.py")
            if inicio != alvo and inicio in self.repo.arquivos and not self.arquivo.startswith(pasta + "/"):
                pacotes.append(inicio)
            pasta = posixpath.dirname(pasta)
        return pacotes

    def avisar(self, no: ast.AST, texto: str) -> None:
        self.resultado.avisos.append(f"{self.arquivo}:{no.lineno}: {texto}: {_trecho(self.fonte, no)}")

    def ler(self) -> None:
        try:
            arvore = ast.parse(self.fonte)
        except SyntaxError as erro:
            self.resultado.erros.append(f"{self.arquivo}:{erro.lineno}: not valid Python for this checker: {erro.msg}")
            return
        partes = tuple(self.arquivo.split("/"))
        nomes = _nomes(arvore, partes)
        chamadas = [no for no in ast.walk(arvore) if isinstance(no, ast.Call)]
        self.bases = self.bases_de(chamadas, partes, nomes)
        self.processos = _processos(arvore)
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                for alias in no.names:
                    self.ligar(no.lineno, self.import_(alias.name.split(".")))
            elif isinstance(no, ast.ImportFrom):
                for alias in no.names:
                    self.ligar(no.lineno, self.from_(no, alias.name))
        for no in chamadas:
            self.chamada(no, partes, nomes)

    def bases_de(self, chamadas: list[ast.Call], partes: tuple[str, ...], nomes: dict) -> list[str]:
        """The folders imports resolve from, in order (see the module docstring)."""
        inseridos, anexados = [], []
        for no in filter(_sys_path, chamadas):
            base = _caminho(no.args[-1], partes, nomes) if no.args else None
            if base is None:
                self.avisar(no, "sys.path entry not resolved, imports through it are not checked")
            else:
                (inseridos if no.func.attr == "insert" else anexados).append("/".join(base))
        pasta = "/".join(partes[:-1])
        proprio = [] if _juntar(pasta, "__init__.py") in self.repo.arquivos else [pasta]
        return [*reversed(inseridos), *proprio, "", *anexados]

    def chamada(self, no: ast.Call, partes: tuple[str, ...], nomes: dict) -> None:
        nome = _nome(no.func)
        primeiro = no.args[0] if no.args else None
        if nome == "exec" and isinstance(no.func, ast.Name):
            lido = _lido(primeiro, partes, nomes)
            alvo = "/".join(lido) if lido else None
            # exec runs that file alone: no package is imported
            self.carga(no, (posixpath.dirname(alvo), alvo) if alvo in self.repo.arquivos else None)
        elif nome == "import_module":
            literal = _modulo_literal(primeiro)
            self.carga(no, self.import_(literal.split(".")) if literal else None)
        elif nome in DYNAMIC:
            self.carga(no, None)
        else:
            self.processo(no)

    def carga(self, no: ast.Call, achado: tuple[str, str] | None) -> None:
        if achado is None:
            self.avisar(no, "dynamic load, not checked")
        else:
            self.ligar(no.lineno, achado)

    def processo(self, no: ast.Call) -> None:
        """A call that starts a process is silent only when its program is a literal that is neither
        python, dataforge nor a shell."""
        chamado = self.chamado(no)
        if chamado is None:
            return
        modulo, nome = chamado
        if modulo == "os" and nome in ALWAYS:
            self.avisar(no, "subprocess, not checked")
            return
        if not _lancador(modulo, nome):
            return
        texto = _programa(no, nome)
        programa = posixpath.basename(texto.split()[0]).removesuffix(".exe") if texto and texto.split() else None
        if programa is None or programa in SHELLS or re.search(r"python|dataforge", texto):
            self.avisar(no, "subprocess, not checked")

    def chamado(self, no: ast.Call) -> tuple[str, str] | None:
        """(module, function) of a call to a function of subprocess, os or asyncio, or None."""
        modulos, funcoes = self.processos
        if isinstance(no.func, ast.Attribute) and isinstance(no.func.value, ast.Name) and no.func.value.id in modulos:
            return modulos[no.func.value.id], no.func.attr
        if isinstance(no.func, ast.Name) and no.func.id in funcoes:
            return funcoes[no.func.id]
        return None


def _sys_path(no: ast.Call) -> bool:
    """A call sys.path.insert(...) or sys.path.append(...)."""
    return (isinstance(no.func, ast.Attribute) and no.func.attr in ("insert", "append")
            and isinstance(no.func.value, ast.Attribute) and no.func.value.attr == "path"
            and isinstance(no.func.value.value, ast.Name) and no.func.value.value.id == "sys")


def _lido(primeiro: ast.expr | None, partes: tuple[str, ...], nomes: dict) -> tuple[str, ...] | None:
    """The path of the x in exec(x.read_text(...)), or None."""
    if isinstance(primeiro, ast.Call) and isinstance(primeiro.func, ast.Attribute) and primeiro.func.attr == "read_text":
        return _caminho(primeiro.func.value, partes, nomes)
    return None


def _modulo_literal(primeiro: ast.expr | None) -> str | None:
    """The module name of import_module("<literal>"), unless relative."""
    if isinstance(primeiro, ast.Constant) and isinstance(primeiro.value, str) and not primeiro.value.startswith("."):
        return primeiro.value
    return None


def _lancador(modulo: str, nome: str) -> bool:
    """A function that starts a process (os.system and os.popen apart)."""
    return ((modulo == "subprocess" and nome in SUBPROCESS) or (modulo == "asyncio" and nome in ASYNCIO)
            or (modulo == "os" and nome.startswith(("exec", "spawn"))))


def _programa(no: ast.Call, nome: str) -> str | None:
    """The program of a process call, lowercased, when it is a string literal (or the first item of a
    list or tuple of them)."""
    posicao = 1 if nome.startswith("spawn") else 0  # os.spawn*(mode, path, ...)
    comando = no.args[posicao] if len(no.args) > posicao else None
    if isinstance(comando, (ast.List, ast.Tuple)) and comando.elts:
        comando = comando.elts[0]
    return comando.value.lower() if isinstance(comando, ast.Constant) and isinstance(comando.value, str) else None


# TS and JS


def _sem_comentarios(fonte: str) -> str:
    """The source with each comment replaced by its newlines; a string, even one holding '//', is kept."""
    partes, copiado, pos = [], 0, 0
    while inicio := TS_TOKEN_START.search(fonte, pos):
        token = TS_TOKEN[inicio[0]].match(fonte, inicio.start())
        if token is None:  # an unclosed string or comment: go on from the next character
            pos = inicio.start() + 1
            continue
        if inicio[0].startswith("/"):
            partes += [fonte[copiado:token.start()], "\n" * token[0].count("\n")]
            copiado = token.end()
        pos = token.end()
    return "".join([*partes, fonte[copiado:]])


def _ts_from(fonte: str) -> Iterator[tuple[int, str]]:
    """(start, specifier) of each import/export ... from '...', left to right and not overlapping."""
    fim = 0
    for chave in TS_FROM_KEYWORD.finditer(fonte):
        if chave.start() < fim:
            continue
        clausula = TS_FROM_CLAUSE.match(fonte, chave.end())
        palavra = chave.end() + len(clausula[0].rstrip()) - len("from")
        especificador = TS_SPECIFIER.match(fonte, clausula.end())
        if palavra >= chave.end() and TS_FROM_WORD.match(fonte, palavra) and especificador:
            fim = especificador.end()
            yield chave.start(), especificador[2]


def _ts(repo: Repo, arquivo: str, fonte: str, resultado: Resultado) -> None:
    fonte = _sem_comentarios(fonte)
    achados = sorted({*_ts_from(fonte), *((m.start(), m[2]) for padrao in TS_IMPORTS for m in padrao.finditer(fonte))})
    literais = {m.start() for m in TS_IMPORTS[1].finditer(fonte)}
    for m in TS_DYNAMIC.finditer(fonte):
        if m.start() not in literais:
            linha = fonte.count("\n", 0, m.start()) + 1
            resultado.avisos.append(f"{arquivo}:{linha}: dynamic import, not checked: "
                                    f"{fonte.splitlines()[linha - 1].strip()[:SNIPPET]}")
    for inicio, especificador in achados:
        if not especificador.startswith(("./", "../")):
            continue
        linha = fonte.count("\n", 0, inicio) + 1
        base = _normal(_juntar(posixpath.dirname(arquivo), especificador))
        candidatos = [] if base is None else [base, *(base + s for s in TS_SUFFIXES),
                                              *(_juntar(base, "index" + s) for s in TS_SUFFIXES)]
        alvo = next((c for c in candidatos if c in repo.arquivos), None)
        if alvo is None:
            resultado.avisos.append(f"{arquivo}:{linha}: relative import not resolved to a tracked file: "
                                    f"{especificador!r}")
        else:
            resultado.imports.append((arquivo, linha, alvo, None))


def _shell(arquivo: str, dados: bytes, resultado: Resultado) -> None:
    """A .sh or .ps1: one warning per line that names dataforge or python, comments aside."""
    try:
        texto = dados.decode("utf-16" if dados.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig")
    except UnicodeDecodeError:
        resultado.avisos.append(f"{arquivo}: not UTF-8 or UTF-16 text, its commands are not listed")
        return
    for numero, linha in enumerate(texto.splitlines(), 1):
        if not linha.strip().startswith("#") and SHELL_CALL.search(linha):
            resultado.avisos.append(f"{arquivo}:{numero}: command, not checked: {linha.strip()[:SNIPPET]}")


def ler_imports(repo: Repo, resultado: Resultado) -> None:
    for arquivo in resultado.donos:
        dados = (repo.raiz / arquivo).read_bytes()
        if arquivo.endswith((".sh", ".ps1")):
            _shell(arquivo, dados, resultado)
            continue
        try:
            fonte = dados.decode("utf-8-sig")
        except UnicodeDecodeError:
            resultado.erros.append(f"{arquivo}: not UTF-8 text")
            continue
        if arquivo.endswith(".py"):
            _Leitor(repo, arquivo, fonte, resultado).ler()
        else:
            _ts(repo, arquivo, fonte, resultado)


def _posicao(linha: str) -> tuple[str, int]:
    """(file, line) of a line that starts with 'file:line:' or 'file:'."""
    achado = POSICAO.match(linha)
    return (achado[1], int(achado[2] or 0)) if achado else (linha, 0)


def conferir(modelo_: dict, raiz: Path, arquivos) -> Resultado:
    repo = Repo.de(raiz, arquivos)
    elementos = modelo_.get("elements") or []
    resultado = Resultado(donos=donos_do_hoje(elementos, repo))
    ler_imports(repo, resultado)
    por_arquivo = resultado.erros + checar_setas(resultado, elementos, modelo_.get("relations") or [])
    resultado.erros = (checar_caminhos(modelo_, repo) + checar_repetidos(elementos) + checar_dentro(elementos)
                       + checar_componentes(resultado.donos, elementos) + sorted(por_arquivo, key=_posicao))
    resultado.avisos.sort(key=_posicao)
    return resultado


def rodar(modelo_: dict, raiz: Path, arquivos) -> tuple[int, list[str]]:
    """The exit code and the lines `main` prints before its summary."""
    resultado = conferir(modelo_, raiz, arquivos)
    linhas = [f"warning: {a}" for a in resultado.avisos] + [f"error: {e}" for e in resultado.erros]
    return (1 if resultado.erros else 0), linhas


def _n(quantos: int, palavra: str) -> str:
    return f"{quantos} {palavra}{'' if quantos == 1 else 's'}"


def python_do_repo(raiz: Path) -> tuple[int, int]:
    """The major.minor of raiz/.python-version ("3.15" or "3.15.2")."""
    maior, menor = (raiz / ".python-version").read_text(encoding="utf-8").split()[0].split(".")[:2]
    return int(maior), int(menor)


def _python() -> tuple[int, int]:
    return sys.version_info[:2]


def main() -> int:
    precisa, este = python_do_repo(REPO), _python()
    if este < precisa:
        print(f"componentes.py needs Python {precisa[0]}.{precisa[1]} (.python-version) to parse the repo's code, "
              f"not {este[0]}.{este[1]}: run uv run --no-project python scripts/arquitetura/componentes.py")
        return 2
    resultado = conferir(modelo.load(), REPO, arquivos_do_repo(REPO))
    for linha in [f"warning: {a}" for a in resultado.avisos] + [f"error: {e}" for e in resultado.erros]:
        print(linha)
    print(f"componentes.py: {_n(len(resultado.donos), 'code file')} owned in the hoje, "
          f"{_n(len(resultado.imports), 'import')} read; {_n(len(resultado.erros), 'error')}, "
          f"{_n(len(resultado.avisos), 'warning')}")
    return 1 if resultado.erros else 0


if __name__ == "__main__":
    sys.exit(main())
