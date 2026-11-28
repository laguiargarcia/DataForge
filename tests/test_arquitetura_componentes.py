"""scripts/arquitetura/componentes.py: the component model (C4 level 3) checked against the code
(df-8x0m.23.47.2). Each test builds a small model, in the layout of arquitetura/modelo.json, and a
tree of files in tmp_path, and asserts the exit code and every line the check prints."""

import ast
import codecs
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("componentes", ROOT / "scripts" / "arquitetura" / "componentes.py")
componentes = importlib.util.module_from_spec(_spec)
sys.modules["componentes"] = componentes
_spec.loader.exec_module(componentes)

APP = "casaHoje.appHoje"
A, B = f"{APP}.aHoje", f"{APP}.bHoje"


def el(id_: str, codigo=None, kind: str = "componente") -> dict:
    return {"id": id_, "kind": kind, "metadata": {} if codigo is None else {"codigo": codigo}}


def box(id_: str, codigo=None) -> dict:
    return el(id_, codigo, kind="conteiner")


def rel(source: str, target: str, codigo=None) -> dict:
    return {"id": f"{source} -> {target}", "source": source, "target": target,
            "metadata": {} if codigo is None else {"codigo": codigo}}


def run(tmp_path: Path, elements, files: dict, relations=(), deployment=None):
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    modelo = {"elements": list(elements), "relations": list(relations),
              "deployment": deployment or {"nodes": [], "instances": [], "relations": []}}
    return componentes.rodar(modelo, tmp_path, sorted(files))


def two_components(extra=()):
    """Container appHoje [app] with components aHoje [app/a] and bHoje [app/b], plus `extra`."""
    return [box(APP, ["app"]), el(A, ["app/a"]), el(B, ["app/b"]), *extra]


def needs_arrow(file: str, line: int, target: str, source: str = A, dest: str = B) -> str:
    return f"error: {file}:{line}: imports {target}, so the model needs an arrow {source} -> {dest}"


# (1) every codigo path is a tracked file or a folder with tracked files


def test_a_codigo_path_that_is_not_tracked_fails(tmp_path):
    assert run(tmp_path, [box(APP, ["app/nada.py"])], {"app/a.py": ""}) == (
        1, [f"error: {APP}: codigo 'app/nada.py' is not a tracked file or a folder with tracked files"])


def test_a_tracked_file_and_a_folder_with_tracked_files_pass(tmp_path):
    assert run(tmp_path, [box(APP, ["app/a.py", "lib"])], {"app/a.py": "", "lib/x/y.txt": ""}) == (0, [])


def test_a_folder_that_holds_only_untracked_files_fails(tmp_path):
    """The file list is git's (A1): a folder on disk with only untracked files (.venv, docs/) does
    not exist for the CI checkout."""
    (tmp_path / "solto").mkdir()
    (tmp_path / "solto" / "x.py").write_text("", encoding="utf-8")
    assert run(tmp_path, [box(APP, ["solto"])], {"app/a.py": ""}) == (
        1, [f"error: {APP}: codigo 'solto' is not a tracked file or a folder with tracked files"])


def test_a_codigo_of_one_item_is_a_string(tmp_path):
    """LikeC4 1.61.1 exports `codigo ['x']` as the string 'x' (A2)."""
    assert run(tmp_path, [box(APP, "app"), box("casaHoje.libHoje", "lib")], {"app/a.py": ""}) == (
        1, ["error: casaHoje.libHoje: codigo 'lib' is not a tracked file or a folder with tracked files"])


def test_the_codigo_of_arrows_and_deployment_items_is_checked(tmp_path):
    deployment = {"nodes": [el("prod.pc", ["nada"], kind="maquina")], "instances": [], "relations": []}
    assert run(tmp_path, [box(APP, ["app"])], {"app/a.py": ""}, relations=[rel(APP, "casaHoje.x", ["sumiu.py"])],
               deployment=deployment) == (
        1, [f"error: {APP} -> casaHoje.x: codigo 'sumiu.py' is not a tracked file or a folder with tracked files",
            "error: prod.pc: codigo 'nada' is not a tracked file or a folder with tracked files"])


def test_the_codigo_of_the_alvo_is_checked_too(tmp_path):
    assert run(tmp_path, [box("casaAlvo.appAlvo", ["nada"])], {"app/a.py": ""}) == (
        1, ["error: casaAlvo.appAlvo: codigo 'nada' is not a tracked file or a folder with tracked files"])


@pytest.mark.parametrize(("path", "normal"), [("./app", "'app'"), ("app/", "'app'"), ("/app", "'app'"),
                                              ("app//a.py", "'app/a.py'"), ("app/../app", "'app'"), ("..", "none")])
def test_a_codigo_path_not_in_normal_form_fails(tmp_path, path, normal):
    assert run(tmp_path, [box(APP, [path])], {"app/a.py": ""}) == (
        1, [f"error: {APP}: codigo {path!r} is not a repo path in normal form (normal form: {normal})"])


# (5) one path, one owner


def test_the_same_path_in_two_elements_of_one_system_fails(tmp_path):
    assert run(tmp_path, [box(APP, ["app"]), el(A, ["app/a"]), el(B, ["app/a"])], {"app/a/x.py": ""}) == (
        1, [f"error: 'app/a' is in the codigo of {A} and {B}: give it one owner"])


def test_the_same_path_in_the_hoje_and_in_the_alvo_passes(tmp_path):
    assert run(tmp_path, [box(APP, ["app"]), box("casaAlvo.appAlvo", ["app"])], {"app/a.py": ""}) == (0, [])


# (4) in a container with components, a component owns every code file


def test_a_loose_code_file_in_a_container_with_components_fails(tmp_path):
    assert run(tmp_path, two_components(), {"app/a/x.py": "", "app/b/y.py": "", "app/solto.py": ""}) == (
        1, [f"error: app/solto.py: its owner {APP} has components, so one of them must own it"])


def test_a_path_owns_only_the_folder_it_names_not_a_longer_name(tmp_path):
    """codigo 'app/a' holds app/a/x.py, not app/ab/x.py."""
    assert run(tmp_path, [box(APP, ["app"]), el(A, ["app/a"])], {"app/a/x.py": "", "app/ab/x.py": ""}) == (
        1, [f"error: app/ab/x.py: its owner {APP} has components, so one of them must own it"])


def test_a_container_without_components_owns_its_files(tmp_path):
    assert run(tmp_path, [box(APP, ["app"])], {"app/solto.py": ""}) == (0, [])


def test_a_file_of_a_container_nested_by_path_is_not_the_outer_containers(tmp_path):
    """Containers nest by path, not in the model (A9): app/sub belongs to the sibling subHoje."""
    elements = [box(APP, ["app"]), el(A, ["app/a"]), box("casaHoje.subHoje", ["app/sub"])]
    assert run(tmp_path, elements, {"app/a/x.py": "", "app/sub/y.py": "", "app/solto.py": ""}) == (
        1, [f"error: app/solto.py: its owner {APP} has components, so one of them must own it"])


@pytest.mark.parametrize("path", ["app/s.py", "app/s.ts", "app/s.tsx", "app/s.js", "app/s.jsx", "app/s.mjs",
                                  "app/s.cjs", "app/s.sh", "app/s.ps1"])
def test_each_code_suffix_needs_a_component(tmp_path, path):
    assert run(tmp_path, two_components(), {"app/a/x.py": "", "app/b/y.py": "", path: ""}) == (
        1, [f"error: {path}: its owner {APP} has components, so one of them must own it"])


@pytest.mark.parametrize("path", ["app/README.md", "app/conf.yaml", "app/tests/x.py", "app/test/x.py",
                                  "app/__tests__/x.ts", "app/e2e/x.ts", "app/dist/assets/i.js",
                                  "app/node_modules/m/i.js", "app/test_x.py", "app/x_test.py", "app/conftest.py",
                                  "app/x.test.ts", "app/x.spec.ts", "app/vite.config.ts", "app/p.config.js",
                                  "app/p.config.mjs", "app/p.config.cjs"])
def test_files_out_of_scope_need_no_component(tmp_path, path):
    assert run(tmp_path, two_components(), {"app/a/x.py": "", "app/b/y.py": "", path: ""}) == (0, [])


def test_the_alvo_is_not_checked_against_the_code(tmp_path):
    """Rules (3) and (4) run on the hoje only: the alvo draws code that does not exist yet (A11)."""
    elements = [box("casaAlvo.appAlvo", ["app"]), el("casaAlvo.appAlvo.aAlvo", ["app/a"]),
                el("casaAlvo.appAlvo.bAlvo", ["app/b"])]
    files = {"app/a/x.py": "import app.b.y\n", "app/b/y.py": "", "app/solto.py": ""}
    assert run(tmp_path, elements, files) == (0, [])


# (3) an import between owners has an arrow


def test_an_import_with_its_arrow_passes(tmp_path):
    files = {"app/a/x.py": "import app.b.y\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files, relations=[rel(A, B)]) == (0, [])


def test_an_import_without_an_arrow_fails(tmp_path):
    files = {"app/a/x.py": "import app.b.y\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files, relations=[rel(B, A)]) == (
        1, [needs_arrow("app/a/x.py", 1, "app/b/y.py")])


def test_an_arrow_at_the_container_does_not_cover_a_components_import(tmp_path):
    lib = "casaHoje.libHoje"
    files = {"app/a/x.py": "import lib.z\n", "app/b/y.py": "", "lib/z.py": "import app.b.y\n"}
    assert run(tmp_path, two_components([box(lib, ["lib"])]), files, relations=[rel(APP, lib), rel(lib, APP)]) == (
        1, [needs_arrow("app/a/x.py", 1, "lib/z.py", A, lib), needs_arrow("lib/z.py", 1, "app/b/y.py", lib, B)])


def test_an_import_between_ancestor_and_descendant_needs_no_arrow(tmp_path):
    """LikeC4 refuses an arrow between parent and child (A3); ancestry is by id, not by path."""
    elements = [box(APP, ["app"]), el(A, ["app/a"]), el(f"{A}.qHoje", ["app/a/q"])]
    files = {"app/a/x.py": "import app.a.q.y\n", "app/a/q/y.py": "import app.a.x\n"}
    assert run(tmp_path, elements, files) == (0, [])


def test_an_import_between_containers_without_components_is_not_checked(tmp_path):
    files = {"app/x.py": "import lib.y\n", "lib/y.py": ""}
    assert run(tmp_path, [box(APP, ["app"]), box("casaHoje.libHoje", ["lib"])], files) == (0, [])


def test_imports_of_out_of_scope_files_and_of_unowned_files_are_not_checked(tmp_path):
    files = {"app/a/test_x.py": "import app.b.y\n", "app/a/x.py": "import app.b.tests.t\nimport solto\n",
             "app/b/y.py": "", "app/b/tests/t.py": "", "solto.py": ""}
    assert run(tmp_path, two_components(), files) == (
        0, ["warning: app/a/x.py:2: imports solto.py, which no element owns"])


# Python imports, resolved to files


@pytest.mark.parametrize(("source", "target"), [
    ("import app.b.y", "app/b/y.py"),
    ("import app.b.y as z", "app/b/y.py"),
    ("from app.b import y", "app/b/y.py"),
    ("from app.b import nome", "app/b/__init__.py"),
    ("from ..b import y", "app/b/y.py"),
    ("from ..b.y import nome", "app/b/y.py"),
])
def test_python_imports_resolve_to_the_imported_file(tmp_path, source, target):
    files = {"app/a/__init__.py": "", "app/a/x.py": f"{source}\n", "app/b/__init__.py": "",
             "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.py", 1, target)])


def test_a_relative_import_of_the_same_package_resolves(tmp_path):
    w = f"{APP}.wHoje"
    files = {"app/a/__init__.py": "", "app/a/x.py": "from . import w\n", "app/a/w.py": "", "app/b/y.py": ""}
    assert run(tmp_path, two_components([el(w, ["app/a/w.py"])]), files) == (
        1, [needs_arrow("app/a/x.py", 1, "app/a/w.py", A, w)])


def test_an_import_inside_a_function_counts(tmp_path):
    files = {"app/a/x.py": "def f():\n    import app.b.y\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.py", 2, "app/b/y.py")])


def test_a_script_imports_a_sibling_of_its_folder(tmp_path):
    w = f"{APP}.wHoje"
    files = {"app/a/x.py": "import w\n", "app/a/w.py": "", "app/b/y.py": ""}
    assert run(tmp_path, two_components([el(w, ["app/a/w.py"])]), files) == (
        1, [needs_arrow("app/a/x.py", 1, "app/a/w.py", A, w)])


def test_a_module_of_a_package_does_not_import_a_sibling_by_bare_name(tmp_path):
    """Python puts a script's folder on sys.path, not a package module's: `import logging` in
    app/a/x.py is the standard library, not app/a/logging.py."""
    w = f"{APP}.wHoje"
    files = {"app/a/__init__.py": "", "app/a/x.py": "import logging\n", "app/a/logging.py": "", "app/b/y.py": ""}
    assert run(tmp_path, two_components([el(w, ["app/a/logging.py"])]), files) == (0, [])


@pytest.mark.parametrize("source", [
    'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "b"))\nimport y\n',
    'PASTA = Path(__file__).absolute().parent.parent / "b"\nsys.path.append(str(PASTA))\nimport y\n',
])
def test_a_static_sys_path_entry_is_a_base_for_imports(tmp_path, source):
    files = {"app/a/x.py": source, "app/b/y.py": ""}
    line = source.count("\n")
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.py", line, "app/b/y.py")])


def test_a_sys_path_entry_that_does_not_resolve_is_a_warning(tmp_path):
    files = {"app/a/x.py": "def f(pasta):\n    sys.path.insert(0, str(pasta))\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (
        0, ["warning: app/a/x.py:2: sys.path entry not resolved, imports through it are not checked: "
            "sys.path.insert(0, str(pasta))"])


@pytest.mark.parametrize(("source", "extra", "expected"), [
    # an inserted entry comes before the script's own folder (app/a/y.py is aHoje's own: no arrow)
    ('sys.path.insert(0, str(Path(__file__).parent.parent / "b"))\nimport y\n', {"app/a/y.py": ""},
     [needs_arrow("app/a/x.py", 2, "app/b/y.py")]),
    # the script's own folder comes before the repo root (w.py at the root has no owner)
    ("import w\n", {"w.py": "", "app/a/w.py": ""}, [needs_arrow("app/a/x.py", 1, "app/a/w.py", A, f"{APP}.wHoje")]),
    # the repo root comes before an appended entry
    ('sys.path.append(str(Path(__file__).parent.parent / "b"))\nimport y\n', {"y.py": ""},
     ["warning: app/a/x.py:2: imports y.py, which no element owns"]),
])
def test_bases_resolve_in_order(tmp_path, source, extra, expected):
    files = {"app/a/x.py": source, "app/a/w.py": "", "app/b/y.py": "", **extra}
    elements = two_components([el(f"{APP}.wHoje", ["app/a/w.py"])])
    assert run(tmp_path, elements, files) == (int(any(e.startswith("error:") for e in expected)), expected)


def test_one_line_that_imports_two_names_of_one_file_is_one_error(tmp_path):
    files = {"app/a/x.py": "from app.b import um, dois\n", "app/b/__init__.py": ""}
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.py", 1, "app/b/__init__.py")])


def test_errors_and_warnings_come_sorted_by_file_and_line(tmp_path):
    """ast.walk visits a function's body after the lines below it; the output is in file order."""
    files = {"app/a/x.py": "def f():\n    import app.b.y\n    subprocess.run(a)\nimport app.b.z\nsubprocess.run(b)\n",
             "app/b/y.py": "", "app/b/z.py": "", "app/b/quebrado.py": "def (:\n"}
    try:
        ast.parse(files["app/b/quebrado.py"])
    except SyntaxError as error:
        quebrado = f"error: app/b/quebrado.py:{error.lineno}: not valid Python for this checker: {error.msg}"
    assert run(tmp_path, two_components(), files) == (1, [
        "warning: app/a/x.py:3: subprocess, not checked: subprocess.run(a)",
        "warning: app/a/x.py:5: subprocess, not checked: subprocess.run(b)",
        needs_arrow("app/a/x.py", 2, "app/b/y.py"), needs_arrow("app/a/x.py", 4, "app/b/z.py"), quebrado])


def run_bytes(tmp_path: Path, files: dict, relations=()):
    """run() with files given as bytes."""
    for path, data in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(data)
    return componentes.rodar({"elements": two_components(), "relations": list(relations)}, tmp_path, sorted(files))


def test_a_python_file_that_is_not_utf8_fails(tmp_path):
    files = {"app/a/x.py": "nome = 'ação'\n".encode("latin-1"), "app/b/y.py": b""}
    assert run_bytes(tmp_path, files) == (1, ["error: app/a/x.py: not UTF-8 text"])


def test_a_python_file_with_a_bom_is_read(tmp_path):
    files = {"app/a/x.py": codecs.BOM_UTF8 + b"import app.b.y\n", "app/b/y.py": b""}
    assert run_bytes(tmp_path, files) == (1, [needs_arrow("app/a/x.py", 1, "app/b/y.py")])


def test_a_shell_line_that_names_dataforge_or_python_is_a_warning(tmp_path):
    script = b"#!/bin/sh\n# runs dataforge every day\nexport DATAFORGE_HOME=/x\ndataforge jobs run ws job\npython3.14 -m x\n"
    files = {"app/a/run.sh": script, "app/b/y.py": b""}
    assert run_bytes(tmp_path, files) == (0, ["warning: app/a/run.sh:4: command, not checked: dataforge jobs run ws job",
                                              "warning: app/a/run.sh:5: command, not checked: python3.14 -m x"])


def test_a_utf16_powershell_script_is_read(tmp_path):
    """PowerShell 5.1 writes UTF-16 with a BOM."""
    files = {"app/a/run.ps1": "Write-Host 'olá'\r\n& dataforge.exe run ws job\r\n".encode("utf-16"), "app/b/y.py": b""}
    assert run_bytes(tmp_path, files) == (0, ["warning: app/a/run.ps1:2: command, not checked: & dataforge.exe run ws job"])


def test_a_shell_script_that_does_not_decode_is_a_warning(tmp_path):
    files = {"app/a/run.sh": "echo 'ação'\n".encode("latin-1"), "app/b/y.py": b""}
    assert run_bytes(tmp_path, files) == (
        0, ["warning: app/a/run.sh: not UTF-8 or UTF-16 text, its commands are not listed"])


def test_a_third_party_import_is_not_a_file(tmp_path):
    files = {"app/a/x.py": "import yaml\nfrom polars import col\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (0, [])


def test_a_file_that_does_not_parse_fails(tmp_path):
    source = "def (:\n"
    try:
        ast.parse(source)
    except SyntaxError as error:
        expected = f"error: app/a/x.py:{error.lineno}: not valid Python for this checker: {error.msg}"
    assert run(tmp_path, two_components(), {"app/a/x.py": source, "app/b/y.py": ""}) == (1, [expected])


# Loads: a literal target is an import; any other is a warning


@pytest.mark.parametrize("source", [
    'exec((Path(__file__).parent.parent / "b" / "y.py").read_text(encoding="utf-8"))\n',
    'importlib.import_module("app.b.y")\n',
])
def test_a_load_of_a_literal_target_is_an_import(tmp_path, source):
    files = {"app/a/x.py": source, "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.py", 1, "app/b/y.py")])


@pytest.mark.parametrize("source", [
    'exec("x = 1")',
    'exec(codigo)',
    'importlib.import_module(nome)',
    'importlib.import_module("edicao")',
    'importlib.util.spec_from_file_location("m", caminho)',
    'runpy.run_path(caminho)',
    '__import__("app.b.y")',
])
def test_a_dynamic_load_is_a_warning(tmp_path, source):
    files = {"app/a/x.py": source + "\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (
        0, [f"warning: app/a/x.py:1: dynamic load, not checked: {source}"])


@pytest.mark.parametrize("source", [
    'subprocess.run([sys.executable, "-m", "dataforge"])',
    'subprocess.run(cmd)',
    'subprocess.Popen(["dataforge", "run", "ws", "job"])',
    'subprocess.run("python x.py", shell=True)',
    'run(["python", "x.py"])',
    'subprocess.check_output(["powershell.exe", "-Command", script])',
    'subprocess.call(["/bin/sh", "-c", texto])',
])
def test_a_subprocess_that_may_run_python_is_a_warning(tmp_path, source):
    files = {"app/a/x.py": f"from subprocess import run\n{source}\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (
        0, [f"warning: app/a/x.py:2: subprocess, not checked: {source}"])


@pytest.mark.parametrize(("source", "line"), [
    ("import subprocess as sp\nsp.run(cmd)", "sp.run(cmd)"),
    ("from subprocess import check_output as saida\nsaida(cmd)", "saida(cmd)"),
    ('import os\nos.system("ls")', 'os.system("ls")'),
    ('import os\nos.popen("ls")', 'os.popen("ls")'),
    ("import os\nos.execv(programa, argumentos)", "os.execv(programa, argumentos)"),
    ("import os\nos.spawnv(os.P_WAIT, programa, argumentos)", "os.spawnv(os.P_WAIT, programa, argumentos)"),
    ("import asyncio\nasyncio.create_subprocess_exec(programa)", "asyncio.create_subprocess_exec(programa)"),
    ("from asyncio import create_subprocess_shell\ncreate_subprocess_shell(texto)", "create_subprocess_shell(texto)"),
])
def test_each_way_to_start_a_process_is_read(tmp_path, source, line):
    files = {"app/a/x.py": source + "\n", "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (0, [f"warning: app/a/x.py:2: subprocess, not checked: {line}"])


def test_a_subprocess_of_a_literal_other_program_is_silent(tmp_path):
    source = ('subprocess.run(["git", "status"], check=True)\nsubprocess.check_call(["launchctl", "load", str(p)])\n'
              'subprocess.run("crontab -l", shell=True)\nos.run(cmd)\napp.exec()\n'
              'os.spawnl(os.P_WAIT, "/usr/bin/git", "git")\nos.getcwd()\n')
    files = {"app/a/x.py": source, "app/b/y.py": ""}
    assert run(tmp_path, two_components(), files) == (0, [])


# TS and JS imports


@pytest.mark.parametrize(("source", "line"), [
    ("import { x,\n  y } from '../b/m'\n", 1),
    ('import type { T } from "../b/m"\n', 1),
    ("const z = 1\nimport '../b/m'\n", 2),
    ("export { x } from '../b/m.tsx'\n", 1),
    ("const m = await import('../b/m')\n", 1),
    ("import {\n  a, // the 'a'\n  /* b, */ c,\n} from '../b/m'\n", 1),
    ("// import x from '../b/sumiu'\n/* import '../b/sumiu' */\nimport x from '../b/m'\n", 3),
])
def test_ts_imports_resolve_to_the_imported_file(tmp_path, source, line):
    files = {"app/a/x.tsx": source, "app/b/m.tsx": ""}
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.tsx", line, "app/b/m.tsx")])


def test_a_ts_import_of_a_folder_resolves_to_its_index(tmp_path):
    files = {"app/a/x.ts": "import { y } from '../b'\n", "app/b/index.ts": ""}
    assert run(tmp_path, two_components(), files) == (1, [needs_arrow("app/a/x.ts", 1, "app/b/index.ts")])


def test_ts_imports_of_packages_and_assets_are_not_code(tmp_path):
    files = {"app/a/x.ts": "import React from 'react'\nimport '../b/estilo.css'\n", "app/b/y.ts": "",
             "app/b/estilo.css": ""}
    assert run(tmp_path, two_components(), files) == (0, [])


@pytest.mark.parametrize("source", ["const m = await import(`./x/${n}`)", "import(nome)"])
def test_a_ts_import_of_no_literal_is_a_warning(tmp_path, source):
    files = {"app/a/x.ts": f"{source}\n", "app/b/y.ts": ""}
    assert run(tmp_path, two_components(), files) == (0, [f"warning: app/a/x.ts:1: dynamic import, not checked: {source}"])


def test_a_relative_ts_import_that_does_not_resolve_is_a_warning(tmp_path):
    files = {"app/a/x.ts": "import { y } from '../b/sumiu'\n", "app/b/y.ts": ""}
    assert run(tmp_path, two_components(), files) == (
        0, ["warning: app/a/x.ts:1: relative import not resolved to a tracked file: '../b/sumiu'"])


# The command line


def test_the_files_are_the_ones_git_tracks(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "solto.py").write_text("", encoding="utf-8")
    (tmp_path / "apagado.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.py", "apagado.py"], check=True)
    (tmp_path / "apagado.py").unlink()
    assert componentes.arquivos_do_repo(tmp_path) == ["a.py"]


def test_main_prints_warnings_then_errors_then_a_summary(tmp_path, capsys, monkeypatch):
    files = {"app/a/x.py": "import app.b.y\nexec(codigo)\n", "app/b/y.py": ""}
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    modelo = {"elements": two_components(), "relations": [], "deployment": {"nodes": [], "instances": [], "relations": []}}
    monkeypatch.setattr(componentes, "REPO", tmp_path)
    monkeypatch.setattr(componentes.modelo, "load", lambda: modelo)
    monkeypatch.setattr(componentes, "arquivos_do_repo", lambda raiz: sorted(files))
    assert componentes.main() == 1
    assert capsys.readouterr().out.splitlines() == [
        "warning: app/a/x.py:2: dynamic load, not checked: exec(codigo)",
        needs_arrow("app/a/x.py", 1, "app/b/y.py"),
        "componentes.py: 2 code files owned in the hoje, 1 import read; 1 error, 1 warning",
    ]
