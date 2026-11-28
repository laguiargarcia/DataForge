"""scripts/arquitetura/docs.py: every element of the C4 model links one doc under docs/ that exists
and has a front matter (topic, read_when, code, status), whose code: covers the element's codigo
(df-8x0m.23.39.1). `esbocar` links and sketches the elements without a doc."""

import copy
import shutil
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.arquitetura import docs, modelo

ROOT = Path(__file__).resolve().parents[1]
FM = "---\ntopic: Engine\nread_when: When working on it\ncode:\n  - pkg/engine.py\nstatus: pronto\n---\n\n# Engine\n"


def _element(id_, kind="componente", codigo=None, url=None, title="Caixa", **extra):
    links = [{"url": url, "title": "doc"}] if url else []
    metadata = {"codigo": codigo} if codigo is not None else {}
    return {"id": id_, "kind": kind, "title": title, "description": extra.get("description"),
            "technology": extra.get("technology"), "tags": [], "metadata": metadata, "links": links}


@pytest.fixture
def repo(tmp_path):
    """A fake repository: one code file and one doc with front matter."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "engine.py").write_text("", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "engine.md").write_text(FM, encoding="utf-8")
    return tmp_path


def _model(*elements):
    return {"elements": list(elements)}


def _ok_model():
    return _model(_element("casaHoje.motorHoje", codigo=["pkg/engine.py"], url="../docs/engine.md"),
                  _element("casaAlvo.motorAlvo", codigo="pkg/engine.py", url="../docs/engine.md"))


def test_conferir_passes_on_the_committed_model():
    assert docs.conferir(modelo.load()) == []


def test_conferir_command_exits_0_on_the_committed_model():
    run = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/arquitetura/docs.py"), "conferir"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr


def test_conferir_command_exits_1_naming_the_element_on_a_failing_model(tmp_path):
    """The CLI on a copy of the scripts whose model has an element without a doc link."""
    shutil.copytree(ROOT / "scripts" / "arquitetura", tmp_path / "scripts" / "arquitetura",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "arquitetura").mkdir()
    (tmp_path / "arquitetura" / "modelo.json").write_text(json.dumps(_model(_element("casaHoje.semDocHoje"))),
                                                          encoding="utf-8")
    run = subprocess.run([sys.executable, "-S", str(tmp_path / "scripts/arquitetura/docs.py"), "conferir"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "error: casaHoje.semDocHoje has no doc link" in run.stdout


def test_every_element_of_the_committed_model_has_a_doc():
    m = modelo.load()
    assert set(docs.docs_por_elemento(m)) == {e["id"] for e in m["elements"]}
    assert all(p.startswith("docs/") and (ROOT / p).is_file() for p in docs.docs_por_elemento(m).values())


def test_a_fake_model_that_is_fine_passes(repo):
    assert docs.conferir(_ok_model(), repo) == []


def test_an_element_without_its_link_fails_naming_it():
    m = copy.deepcopy(modelo.load())
    target = next(e for e in m["elements"] if e["id"] == "casaHoje.dataforgeHoje.motorHoje")
    target["links"] = []
    problems = docs.conferir(m)
    assert problems == ["error: casaHoje.dataforgeHoje.motorHoje has no doc link: add link "
                        "../docs/caixas/casa.dataforge.motor.md 'doc' to it "
                        "(python3 scripts/arquitetura/docs.py esbocar does it)"]


def test_a_link_with_another_title_is_not_the_doc(repo):
    m = _ok_model()
    m["elements"][0]["links"] = [{"url": "../docs/engine.md", "title": "e2e: x"}]
    assert [p for p in docs.conferir(m, repo) if "has no doc link" in p] == [
        "error: casaHoje.motorHoje has no doc link: add link ../docs/caixas/casa.motor.md 'doc' to it "
        "(python3 scripts/arquitetura/docs.py esbocar does it)"]


def test_a_link_to_a_missing_doc_fails_naming_the_element(repo):
    m = _ok_model()
    m["elements"][1]["links"][0]["url"] = "../docs/sumiu.md"
    assert docs.conferir(m, repo) == ["error: casaAlvo.motorAlvo: links docs/sumiu.md, which does not exist "
                                      "(python3 scripts/arquitetura/docs.py esbocar writes a sketch)"]


def test_a_link_to_a_doc_without_front_matter_fails_naming_the_element(repo):
    (repo / "docs" / "solto.md").write_text("# Solto\n\ntopic: not front matter\n", encoding="utf-8")
    m = _model(_element("casaHoje.soltoHoje", url="../docs/solto.md"))
    assert docs.conferir(m, repo) == ["error: casaHoje.soltoHoje: docs/solto.md has no front matter "
                                      "(--- on its first line; docs/documentation.md)"]


@pytest.mark.parametrize(("drop", "message"), [
    ("topic", "has no topic"),
    ("read_when", "has no read_when"),
    ("status", "has status (none), not one of esboço, pronto"),
])
def test_a_front_matter_without_a_key_fails(repo, drop, message):
    text = "".join(line + "\n" for line in FM.splitlines() if not line.startswith(f"{drop}:"))
    (repo / "docs" / "engine.md").write_text(text, encoding="utf-8")
    problems = docs.conferir(_ok_model(), repo)
    assert problems == [f"error: casaAlvo.motorAlvo, casaHoje.motorHoje: the front matter of docs/engine.md {message}"]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../fora.py", "pkg/../../fora.py", "", "C:/fora.py", r"\\fora.py",
                                 r"pkg\\..\\fora.py"])
def test_a_code_path_outside_the_repo_or_empty_fails(repo, bad):
    (repo / "docs" / "engine.md").write_text(FM.replace("  - pkg/engine.py\n", f"  - pkg/engine.py\n  - {bad}\n"),
                                            encoding="utf-8")
    problems = docs.conferir(_ok_model(), repo)
    assert problems == [f"error: casaAlvo.motorAlvo, casaHoje.motorHoje: code: of docs/engine.md names {bad!r}, "
                        "not a path inside the repo"]


def test_a_front_matter_without_code_fails(repo):
    (repo / "docs" / "engine.md").write_text(FM.replace("code:\n  - pkg/engine.py\n", ""), encoding="utf-8")
    problems = docs.conferir(_model(_element("casaHoje.xHoje", url="../docs/engine.md")), repo)
    assert problems == ["error: casaHoje.xHoje: the front matter of docs/engine.md has no code: list"]


def test_an_unknown_status_fails(repo):
    (repo / "docs" / "engine.md").write_text(FM.replace("status: pronto", "status: rascunho"), encoding="utf-8")
    assert docs.conferir(_ok_model(), repo) == [
        "error: casaAlvo.motorAlvo, casaHoje.motorHoje: the front matter of docs/engine.md has status rascunho, "
        "not one of esboço, pronto"]


def test_code_that_lacks_a_codigo_path_fails_naming_element_and_path(repo):
    (repo / "pkg" / "dag.py").write_text("", encoding="utf-8")
    m = _ok_model()
    m["elements"][0]["metadata"]["codigo"] = ["pkg/engine.py", "pkg/dag.py"]
    assert docs.conferir(m, repo) == [
        "error: casaHoje.motorHoje: code: of docs/engine.md does not cover its codigo path pkg/dag.py"]


def test_a_folder_in_code_covers_the_files_under_it(repo):
    (repo / "docs" / "engine.md").write_text(FM.replace("  - pkg/engine.py", "  - pkg"), encoding="utf-8")
    assert docs.conferir(_ok_model(), repo) == []
    m = _ok_model()
    m["elements"][0]["metadata"]["codigo"] = ["pkgx/engine.py"]
    assert "does not cover its codigo path pkgx/engine.py" in docs.conferir(m, repo)[0]


def test_a_code_path_that_does_not_exist_fails(repo):
    (repo / "docs" / "engine.md").write_text(FM.replace("  - pkg/engine.py", "  - pkg/engine.py\n  - pkg/sumiu.py"),
                                             encoding="utf-8")
    assert docs.conferir(_ok_model(), repo) == [
        "error: casaAlvo.motorAlvo, casaHoje.motorHoje: code: of docs/engine.md names pkg/sumiu.py, which does not exist"]


@pytest.mark.parametrize("url", ["docs/engine.md", "../README.md", "../docs/engine.txt", "../docs/../x.md",
                                 "../docs/a//b.md", "../docs.md", "https://example.com/docs/x.md"])
def test_a_link_outside_docs_fails(repo, url):
    m = _model(_element("casaHoje.xHoje", url=url))
    assert docs.conferir(m, repo) == [f"error: casaHoje.xHoje: doc link {url!r} is not ../docs/<path>.md"]
    assert docs.docs_por_elemento(m) == {}


def test_two_doc_links_fail(repo):
    m = _ok_model()
    m["elements"][0]["links"].append({"url": "../docs/engine.md", "title": "doc"})
    assert docs.conferir(m, repo) == [
        "error: casaHoje.motorHoje has 2 doc links (../docs/engine.md, ../docs/engine.md): keep one"]


def test_a_sketch_no_element_links_fails(repo):
    (repo / "docs" / "caixas").mkdir()
    (repo / "docs" / "caixas" / "velha.md").write_text(FM, encoding="utf-8")
    assert docs.conferir(_ok_model(), repo) == [
        "error: docs/caixas/velha.md is linked by no element of the model: remove it"]


def test_a_broken_front_matter_fails_naming_the_element(repo):
    (repo / "docs" / "engine.md").write_text(FM.replace("status: pronto", "status:\n  nested: yes"), encoding="utf-8")
    problems = docs.conferir(_ok_model(), repo)
    assert len(problems) == 1
    assert problems[0].startswith("error: casaAlvo.motorAlvo, casaHoje.motorHoje: the front matter of docs/engine.md is broken")


def test_docs_por_elemento_maps_ids_to_repo_paths():
    assert docs.docs_por_elemento(_ok_model()) == {"casaHoje.motorHoje": "docs/engine.md",
                                                   "casaAlvo.motorAlvo": "docs/engine.md"}
    assert docs.docs_por_elemento()["casaHoje.dataforgeHoje.motorHoje"] == "docs/subsystems/engine.md"


def test_ler_front_matter_reads_the_flat_subset(tmp_path):
    doc = tmp_path / "x.md"
    doc.write_text(r"""---
topic: "Arquivos: o \"workspace\""
read_when: 'it''s here'
related_issues: [df-1, df-2]
quoted: ["a, b", 'c, d', e]
plain: valor # a comment
hash: a#b
empty: []
code:
- a/b.py
  - "c d"
status: esboço
# a comment

---
# Title
""", encoding="utf-8")
    assert docs.ler_front_matter(doc) == {"topic": 'Arquivos: o "workspace"', "read_when": "it's here",
                                          "related_issues": ["df-1", "df-2"], "empty": [],
                                          "quoted": ["a, b", "c, d", "e"], "plain": "valor", "hash": "a#b",
                                          "code": ["a/b.py", "c d"], "status": "esboço"}


@pytest.mark.parametrize("text", ["# Title\n", "", "\n---\ntopic: x\n---\n"])
def test_ler_front_matter_is_empty_without_one(tmp_path, text):
    doc = tmp_path / "x.md"
    doc.write_text(text, encoding="utf-8")
    assert docs.ler_front_matter(doc) == {}


@pytest.mark.parametrize("body", ["topic: x\n", "topic: x\nlist:\n  nested: y\n---\n",
                                  "l: [a\"b, c]\n---\n", "l: ['a, b]\n---\n", "l: [a,, b]\n---\n",
                                  "topic: a `type: b`\n---\n", "code:\n  - a: b\n---\n", "- item\n---\n",
                                  'topic: "open\n---\n', "topic: 'open\n---\n", "topic: '\n---\n"])
def test_ler_front_matter_refuses_what_is_not_the_subset(tmp_path, body):
    doc = tmp_path / "x.md"
    doc.write_text("---\n" + body, encoding="utf-8")
    with pytest.raises(ValueError, match=r"x\.md"):
        docs.ler_front_matter(doc)


def test_ler_front_matter_runs_under_python_s(tmp_path):
    """The bead hook (df-8x0m.23.40) runs python3 -S: no site-packages, so no yaml."""
    doc = tmp_path / "x.md"
    doc.write_text(FM.replace("Engine", "Motor de execução"), encoding="utf-8")
    script = tmp_path / "ler.py"
    script.write_text(f"import json, sys\nsys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
                      "from arquitetura import docs\n"
                      "assert 'yaml' not in sys.modules\n"
                      f"print(json.dumps(docs.ler_front_matter({str(doc)!r}), ensure_ascii=False))\n"
                      "print(json.dumps(docs.docs_por_elemento()['casaHoje.dataforgeHoje.motorHoje']))\n",
                      encoding="utf-8")
    run = subprocess.run([sys.executable, "-S", str(script)], capture_output=True, text=True, encoding="utf-8",
                         check=True)
    fm, path = run.stdout.splitlines()
    assert json.loads(fm) == {"topic": "Motor de execução", "read_when": "When working on it",
                              "code": ["pkg/engine.py"], "status": "pronto"}
    assert json.loads(path) == "docs/subsystems/engine.md"


def test_every_linked_doc_renders_its_front_matter_as_yaml():
    """GitHub renders the front matter as YAML: each linked doc's block is valid YAML, and YAML reads
    the same values as the flat reader."""
    for path in sorted(set(docs.docs_por_elemento().values())):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert text.startswith("---\n"), path
        block = text.split("\n---\n", 1)[0].removeprefix("---\n")
        as_yaml = yaml.safe_load(block)
        flat = docs.ler_front_matter(ROOT / path)
        assert {k: as_yaml[k] for k in docs.CHAVES} == {k: flat[k] for k in docs.CHAVES}, path


def test_every_doc_front_matter_reads_as_yaml_does():
    """Every docs/**/*.md that opens with ---, linked or not: the flat reader does not raise and reads
    the same keys and values as PyYAML."""
    for doc in sorted((ROOT / "docs").rglob("*.md")):
        text = doc.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        flat = docs.ler_front_matter(doc)
        as_yaml = yaml.safe_load(text.split("\n---\n", 1)[0].removeprefix("---\n"))
        plain = {k: str(v).lower() if isinstance(v, bool) else v for k, v in as_yaml.items()}  # `transversal: true`
        assert flat == {k: v if isinstance(v, (str, list)) else str(v) for k, v in plain.items()}, doc


C4 = """model {
  casaHoje = sistema 'Casa' {
    description 'A casa {com chaves}'
    motorHoje = componente 'Motor' {
      #muda
      description 'Roda'
      metadata {
        codigo ['pkg/engine.py']
      }
    }
  }
}
"""


def _esbocar_repo(repo):
    (repo / "arquitetura").mkdir()
    (repo / "arquitetura" / "x.c4").write_text(C4, encoding="utf-8")
    return _model(_element("casaHoje", kind="sistema", title="Casa", description="A casa"),
                  _element("casaHoje.motorHoje", codigo=["pkg/engine.py"], title="Motor", technology="Python",
                           description="Roda"),
                  _element("casaAlvo.motorAlvo", codigo=["pkg/engine.py"], title="Motor novo"),
                  _element("casaHoje.ligadoHoje", url="../docs/caixas/ligado.md", title="Ligado"))


def test_esbocar_links_and_sketches_and_is_idempotent(repo):
    m = _esbocar_repo(repo)
    messages = docs.esbocar(m, repo, repo / "arquitetura")
    assert messages == [
        "casaAlvo.motorAlvo: add `link ../docs/caixas/casa.motor.md 'doc'` to its body in arquitetura/ "
        "(its definition was found 0 times)",
        "casaHoje: linked docs/caixas/casa.md in x.c4",
        "casaHoje.motorHoje: linked docs/caixas/casa.motor.md in x.c4",
        "docs/caixas/casa.md: sketch written for casaHoje",
        "docs/caixas/casa.motor.md: sketch written for casaAlvo.motorAlvo, casaHoje.motorHoje",
        "docs/caixas/ligado.md: sketch written for casaHoje.ligadoHoje",
    ]
    c4 = (repo / "arquitetura" / "x.c4").read_text(encoding="utf-8")
    assert "  casaHoje = sistema 'Casa' {\n    link ../docs/caixas/casa.md 'doc'\n    description" in c4
    assert "      #muda\n      link ../docs/caixas/casa.motor.md 'doc'\n      description 'Roda'" in c4
    sketch = repo / "docs" / "caixas" / "casa.motor.md"
    fm = docs.ler_front_matter(sketch)
    assert fm == {"topic": "Motor / Motor novo",
                  "read_when": "Working on the C4 box casaHoje.motorHoje, casaAlvo.motorAlvo or the code it covers",
                  "code": ["pkg/engine.py"], "status": "esboço"}
    assert "`casaHoje.motorHoje`: Roda" in sketch.read_text(encoding="utf-8")
    assert docs.ler_front_matter(repo / "docs" / "caixas" / "casa.md")["code"] == []
    snapshot = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    again = docs.esbocar(m, repo, repo / "arquitetura")  # the model is stale: the links are only in the .c4
    assert again[1:] == ["casaHoje: already linked in x.c4; run python3 scripts/arquitetura/modelo.py gerar",
                         "casaHoje.motorHoje: already linked in x.c4; run python3 scripts/arquitetura/modelo.py gerar"]
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == snapshot


def test_c4_mudou_only_for_a_new_link_or_a_hand_edit(repo):
    m = _esbocar_repo(repo)
    assert docs.c4_mudou(docs.esbocar(m, repo, repo / "arquitetura"))
    second = docs.esbocar(m, repo, repo / "arquitetura")
    assert not docs.c4_mudou([msg for msg in second if "add `link" not in msg])
    assert docs.c4_mudou(second)  # the element whose definition is not found still needs a hand edit
    assert not docs.c4_mudou(["docs/caixas/x.md: sketch written for x"])


def test_esbocar_sketches_pass_conferir(repo):
    m = _esbocar_repo(repo)
    docs.esbocar(m, repo, repo / "arquitetura")
    for element in m["elements"]:
        if not element["links"]:
            element["links"] = [{"url": "../" + docs._padrao(element["id"]), "title": "doc"}]
    assert docs.conferir(m, repo) == []


def test_esbocar_keeps_an_existing_doc(repo):
    m = _model(_element("casaHoje.motorHoje", codigo=["pkg/engine.py"], url="../docs/engine.md"))
    assert docs.esbocar(m, repo, repo) == []
    assert (repo / "docs" / "engine.md").read_text(encoding="utf-8") == FM


def test_the_ci_job_arquitetura_runs_conferir():
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [s for s in ci["jobs"]["arquitetura"]["steps"]
             if "python3 -S scripts/arquitetura/docs.py conferir" in s.get("run", "")]
    assert steps
    assert all(s.get("shell") == "bash" for s in steps)  # bash: pipefail keeps the exit code through tee
