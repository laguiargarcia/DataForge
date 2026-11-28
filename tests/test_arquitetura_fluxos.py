"""scripts/arquitetura/fluxos.py: every dynamic view of the C4 model (a flow, step by step) names
the end-to-end test that exercises it, with a link `../<path> 'e2e: <test name>'`, and that test
exists (df-8x0m.23.41). The flows are today's: their steps use only elements of the hoje model."""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "arquitetura" / "fluxos.py"
_spec = importlib.util.spec_from_file_location("arquitetura_fluxos", SCRIPT)
fluxos = importlib.util.module_from_spec(_spec)
sys.modules["arquitetura_fluxos"] = fluxos
_spec.loader.exec_module(fluxos)

SPEC = "app/e2e/tela.spec.ts"
PY = "tests/test_coisa_e2e.py"
SPEC_TEXT = """import { test } from '@playwright/test'
test('abre a lista e adiciona um item', async ({ page }) => {})
test.describe('edição', () => {
  test("regra nova muda a categoria", async () => {})
  test.skip(`um teste pulado`, async () => {})
})
test('o "Sair?" volta a "Sair" sozinho', async () => {})
"""
PY_TEXT = """def helper():
    pass


def test_alerta_de_falha(tmp_path):
    pass


async def test_assincrono():
    pass
"""


@pytest.fixture
def repo(tmp_path):
    """A fake repository in tmp_path/repo; tmp_path itself is outside it."""
    root = tmp_path / "repo"
    (root / "app" / "e2e").mkdir(parents=True)
    (root / SPEC).write_text(SPEC_TEXT, encoding="utf-8")
    (root / "app" / "e2e" / "notas.md").write_text("test('x')\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / PY).write_text(PY_TEXT, encoding="utf-8")
    (root / "tests" / "test_unidade.py").write_text("def test_soma():\n    pass\n", encoding="utf-8")
    return root


def link(path, name):
    return {"url": f"../{path}", "title": f"e2e: {name}"}


def view(id_="fluxo-lista", links=(), steps=None, type_="dynamic"):
    if steps is None:
        steps = [{"source": "donoHoje", "target": "casaHoje.telasHoje", "title": "abre"}]
    return {"id": id_, "type": type_, "title": id_, "links": list(links), "steps": steps}


def model(*views):
    return {"views": list(views)}


def test_declared_tests_of_a_playwright_spec_and_of_python():
    assert fluxos.declarados(SPEC_TEXT, ".ts") == {
        "abre a lista e adiciona um item", "regra nova muda a categoria", "um teste pulado",
        'o "Sair?" volta a "Sair" sozinho'}
    assert fluxos.declarados(PY_TEXT, ".py") == {"test_alerta_de_falha", "test_assincrono"}


def test_a_describe_is_not_a_test():
    assert "edição" not in fluxos.declarados(SPEC_TEXT, ".ts")


@pytest.mark.parametrize("texto", [
    "// test('x', () => {})\n",
    "const a = 1 // test('x')\n",
    "/* test('x', () => {}) */\n",
    "/*\n * test('x')\n */\n",
    "const s = \"test('x')\"\n",
    "const s = 'nada test(\\'x\\') aqui'\n",
    "const s = `test('x')`\n",
    "if (/x/.test('x')) {}\n",
    "contest('x')\n",
    "$test('x')\n",
], ids=["line-comment", "comment-after-code", "block-comment", "multiline-block-comment", "double-quoted-string",
        "single-quoted-string-with-escapes", "template-string", "method-call", "longer-name", "dollar-name"])
def test_a_name_outside_a_test_call_is_not_a_test(texto):
    assert fluxos.declarados(texto, ".ts") == set()


@pytest.mark.parametrize("texto", [
    "/* " * 34_000,
    "'" + "a" * 100_000,
    '"' + "a" * 100_000,
    "`" + "a" * 100_000,
    "'\\" * 50_000,
    "`\\" * 50_000,
    "test('" * 20_000,
    "test(`" + "a" * 100_000,
    "/*" + "*" * 100_000,
], ids=["block-comments", "single-quote", "double-quote", "backtick", "single-quote-escapes",
        "backtick-escapes", "test-calls", "test-call-backtick", "stars"])
def test_a_large_unclosed_input_reads_in_linear_time(texto):
    """A token that never closes ends at the end of the text (or line): the scan does not restart
    from every character, which took over 10 s on 100 KB before. CPU time, not wall time: a
    scheduler stall under -n auto does not count."""
    start = time.process_time()
    fluxos.declarados(texto, ".ts")
    assert time.process_time() - start < 2


def test_an_unclosed_comment_or_string_hides_what_follows():
    assert fluxos.declarados("/* sem fim\ntest('x', () => {})\n", ".ts") == set()
    assert fluxos.declarados("const s = `sem fim\ntest('x', () => {})\n", ".ts") == set()


@pytest.mark.parametrize("texto", [
    "const r = /`/\nconst s = `test('inventado')`\n",
    "const r = /'/; const t = 'a test(\"inventado\") b'\n",
], ids=["regex-with-a-backtick", "regex-with-a-quote"])
def test_known_limit_a_regex_literal_with_a_quote_can_invent_a_test(texto):
    """Pinned limit (docstring, rule 5): no JS parser, so the regex's quote opens a string and the
    real string's text reads as code. If this changes, update the docstring."""
    assert fluxos.declarados(texto, ".ts") == {"inventado"}


def test_an_unclosed_quote_ends_at_the_line():
    assert fluxos.declarados("const s = 'sem fim\ntest('x', () => {})\n", ".ts") == {"x"}


def test_a_backslash_before_a_newline_continues_the_string():
    assert fluxos.declarados("const s = 'linha \\\ntest(\\'x\\')'\ntest('y', () => {})\n", ".ts") == {"y"}
    assert fluxos.declarados("test('um nome \\\nem duas linhas', () => {})\n", ".ts") == {"um nome em duas linhas"}


def test_a_test_after_a_comment_or_a_string_still_counts():
    texto = ("const u = 'https://exemplo.invalid/a' // um comentário com 'aspas\n"
             "/* bloco * com estrela */ test('depois', () => {})\n"
             "test('it\\'s escapado', () => {})\n")
    assert fluxos.declarados(texto, ".ts") == {"depois", "it's escapado"}


def test_views_tied_to_existing_tests_pass(repo):
    good = model(view(links=[link(SPEC, "abre a lista e adiciona um item")]),
                 view("fluxo-alerta", [link(PY, "test_alerta_de_falha"), link(SPEC, "regra nova muda a categoria")]),
                 view("hoje-conteineres", type_="element"))
    assert fluxos.problemas(good, repo) == []


def test_other_links_of_the_view_do_not_count(repo):
    only_doc = view(links=[{"url": "https://exemplo.invalid/doc", "title": "doc"}])
    assert fluxos.problemas(model(only_doc), repo) == ["fluxo-lista: nenhum link 'e2e: <teste>' para o teste de ponta a ponta"]


@pytest.mark.parametrize(("links", "problem"), [
    ([], "nenhum link 'e2e: <teste>'"),
    ([link("app/e2e/sumiu.spec.ts", "abre a lista e adiciona um item")], "app/e2e/sumiu.spec.ts não existe"),
    ([link(SPEC, "um teste que ninguém escreveu")], f"{SPEC} não declara o teste 'um teste que ninguém escreveu'"),
    ([link(SPEC, "edição")], f"{SPEC} não declara o teste 'edição'"),
    ([link(PY, "helper")], f"{PY} não declara o teste 'helper'"),
    ([link("tests/test_unidade.py", "test_soma")], "tests/test_unidade.py não é um teste de ponta a ponta"),
    ([{"url": "../" + SPEC, "title": "e2e"}], "o título do link é 'e2e: <nome do teste>'"),
    ([{"url": "../" + SPEC, "title": "e2e:  "}], "o título do link é 'e2e: <nome do teste>'"),
    ([{"url": SPEC, "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "../app/../app/e2e/tela.spec.ts", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "../../fora/e2e/x.spec.ts", "title": "e2e: x"}], "começa com ../"),
    ([{"url": f"../{SPEC}#abre", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": f"../{SPEC}?linha=2", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "../app\\e2e\\tela.spec.ts", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "..//app/e2e/tela.spec.ts", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "../app//e2e/tela.spec.ts", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "../app/e2e/", "title": "e2e: abre a lista e adiciona um item"}], "começa com ../"),
    ([{"url": "../app/e2e", "title": "e2e: x"}], "app/e2e não existe"),
    ([link("app/e2e/notas.md", "x")], "app/e2e/notas.md: só sei ler testes .py"),
])
def test_a_view_without_its_test_fails(repo, links, problem):
    found = fluxos.problemas(model(view(links=links)), repo)
    assert len(found) == 1, found
    assert found[0].startswith("fluxo-lista: ")
    assert problem in found[0]


def test_each_bad_link_is_one_problem(repo):
    links = [link(SPEC, "abre a lista e adiciona um item"), link(SPEC, "não existe"), link("app/e2e/x.spec.ts", "y")]
    assert len(fluxos.problemas(model(view(links=links)), repo)) == 2


def test_an_e2e_file_that_is_not_utf8_is_one_problem(repo):
    (repo / "app" / "e2e" / "latin1.spec.ts").write_bytes("test('ação', () => {})\n".encode("latin-1"))
    assert fluxos.problemas(model(view(links=[link("app/e2e/latin1.spec.ts", "ação")])), repo) == [
        "fluxo-lista: app/e2e/latin1.spec.ts não é texto UTF-8"]


def test_a_symlink_out_of_the_repo_is_refused(repo, tmp_path):
    outside = tmp_path / "x.spec.ts"
    outside.write_text("test('x', () => {})\n", encoding="utf-8")
    (repo / "app" / "e2e" / "x.spec.ts").symlink_to(outside)
    assert fluxos.problemas(model(view(links=[link("app/e2e/x.spec.ts", "x")])), repo) == [
        "fluxo-lista: app/e2e/x.spec.ts não existe no repositório"]


@pytest.mark.parametrize(("steps", "problem"), [
    ([], "a vista não tem passos"),
    ([{"source": "donoHoje", "target": "casaAlvo.servidorAlvo", "title": "usa"}],
     "o passo donoHoje -> casaAlvo.servidorAlvo usa um elemento fora do modelo de hoje"),
    ([{"source": "donoAlvo", "target": "casaHoje.telasHoje", "title": "usa"}],
     "o passo donoAlvo -> casaHoje.telasHoje usa um elemento fora do modelo de hoje"),
])
def test_a_flow_is_of_todays_model(repo, steps, problem):
    found = fluxos.problemas(model(view(links=[link(SPEC, "abre a lista e adiciona um item")], steps=steps)), repo)
    assert found == [f"fluxo-lista: {problem}"]


def test_the_committed_model_passes():
    """Every dynamic view of arquitetura/ names an e2e test that exists, and there is one per main flow."""
    real = fluxos.modelo.load()
    assert fluxos.problemas(real) == []
    dynamic = {v["id"] for v in real["views"] if v["type"] == "dynamic"}
    assert {"fluxo-entrar", "fluxo-lista", "fluxo-desejos", "fluxo-corrigir-categoria-ninho",
            "fluxo-resumo-financas", "fluxo-regra-financas"} <= dynamic


def test_the_command_takes_no_argument_and_exits_by_the_result():
    run = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert (run.returncode, run.stderr) == (0, ""), run.stdout
    assert "dynamic" in run.stdout or "vista" in run.stdout
    bad = subprocess.run([sys.executable, str(SCRIPT), "arquitetura/modelo.json"], capture_output=True, text=True, check=False)
    assert bad.returncode == 2


def test_main_prints_one_line_per_problem_and_exits_1(monkeypatch, capsys, repo):
    monkeypatch.setattr(fluxos, "REPO", repo)
    monkeypatch.setattr(fluxos.modelo, "load", lambda: model(view(), view("fluxo-b", [link(SPEC, "não há")])))
    assert fluxos.main([]) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert out[0].startswith("error: fluxo-b: ")
    assert out[1].startswith("error: fluxo-lista: ")
