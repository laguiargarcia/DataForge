"""scripts/arquitetura/modelo.py: arquitetura/modelo.json, the small sorted JSON of the LikeC4 model
(df-8x0m.23.43). The raw dicts below have the shapes `likec4 export json --skip-layout` gives (at LIKEC4);
the CI job `arquitetura` runs the real export (`conferir`)."""

import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

from dataforge import yamlio
from scripts.arquitetura import modelo

ROOT = Path(__file__).resolve().parents[1]


def _raw() -> dict:
    """An export of a model with one of each thing: metadata, links, tags, a relation kind, element,
    dynamic and deployment views, deployment nodes and instances."""
    return {
        "_stage": "computed", "projectId": "default", "project": {"id": "default", "title": "default"},
        "specification": {
            "tags": {"novo": {"color": "grass"}, "muda": {"color": "tomato"}},
            "elements": {"sistema": {"notation": "Sistema", "style": {}}, "pessoa": {"style": {}}},
            "relationships": {"leitura": {"line": "dotted"}},
            "deployments": {"maquina": {"style": {}}, "ambiente": {"style": {}}},
            "metadataKeys": ["bead"], "customColors": {},
        },
        "elements": {
            "s.s2": {"notation": "Sistema", "style": {}, "tags": ["muda"], "title": "Filho", "kind": "sistema", "id": "s.s2"},
            "p": {"metadata": {"doc": "docs/x.md"}, "style": {"shape": "person"},
                  "links": [{"url": "https://example.com/a", "title": "Doc A"},
                            {"url": "../docs/x.md", "relative": "file:///home/someone/docs/x.md"}],
                  "tags": ["novo", "muda"], "technology": "olhos", "description": {"txt": "desc"},
                  "title": "Pessoa", "kind": "pessoa", "id": "p"},
            "s": {"style": {}, "title": "Sistema", "kind": "sistema", "id": "s", "description": {"md": "**md**"}},
        },
        "relations": {
            "10wqd61": {"line": "dotted", "metadata": {"bead": "df-1"}, "description": {"txt": "seta"},
                        "technology": "HTTPS", "links": [{"url": "https://example.com/r"}], "tags": ["novo"],
                        "title": "lê", "source": {"model": "p"}, "target": {"model": "s"}, "kind": "leitura",
                        "id": "10wqd61"},
            "14yrxbb": {"source": {"model": "p"}, "target": {"model": "s.s2"}, "id": "14yrxbb", "title": "usa"},
            "1exzn7k": {"source": {"model": "p"}, "target": {"model": "s.s2"}, "id": "1exzn7k"},
        },
        "globals": {"predicates": {}, "dynamicPredicates": {}, "styles": {}},
        "views": {
            "index": {"_type": "element", "tags": ["novo"], "links": [{"url": "https://example.com/v"}],
                      "_stage": "computed", "sourcePath": "modelo.c4", "description": {"txt": "vista"},
                      "title": "Índice", "id": "index", "autoLayout": {"direction": "TB"}, "hash": "abc",
                      "nodes": [{"id": "p"}], "edges": [{"id": "e1", "source": "p", "target": "s", "label": "lê"}]},
            "fluxo": {"_type": "dynamic", "tags": None, "links": None, "description": None, "title": "Fluxo",
                      "id": "fluxo", "variant": "diagram", "flow": ["step-01", "step-02"], "nodes": [],
                      "edges": [{"id": "step-01", "source": "p", "target": "s", "label": "pede", "relations": ["10wqd61"]},
                                {"id": "step-02", "source": "s", "target": "p", "label": None}]},
            "dentro": {"_type": "element", "viewOf": "s", "title": "Sistema", "id": "dentro"},
            "impl": {"_type": "deployment", "title": "Implantação", "id": "impl", "nodes": [], "edges": []},
        },
        "deployments": {
            "elements": {
                "prod": {"style": {}, "kind": "ambiente", "tags": ["novo"], "title": "Prod", "id": "prod"},
                "prod.pc": {"style": {}, "kind": "maquina", "technology": "Windows",
                            "metadata": {"servidoPor": "prod"}, "title": "PC", "id": "prod.pc"},
                "prod.pc.inst": {"id": "prod.pc.inst", "element": "s.s2", "title": "Filho no PC", "style": {}},
                "prod.pc.s": {"id": "prod.pc.s", "element": "s", "style": {}},
            },
            "relations": {"2vs8yc": {"title": "copia", "source": {"deployment": "prod.pc.inst"},
                                     "target": {"deployment": "prod.pc"}, "id": "2vs8yc"}},
        },
        "imports": {}, "manualLayouts": {},
    }


def test_elements_keep_the_model_and_drop_the_drawing():
    elements = modelo.normalizar(_raw())["elements"]
    assert [e["id"] for e in elements] == ["p", "s", "s.s2"]
    assert elements[0] == {
        "id": "p", "kind": "pessoa", "title": "Pessoa", "description": "desc", "technology": "olhos",
        "tags": ["muda", "novo"], "metadata": {"doc": "docs/x.md"},
        "links": [{"url": "https://example.com/a", "title": "Doc A"}, {"url": "../docs/x.md", "title": None}],
    }
    assert elements[1]["description"] == "**md**"
    assert elements[2] == {"id": "s.s2", "kind": "sistema", "title": "Filho", "description": None,
                           "technology": None, "tags": ["muda"], "metadata": {}, "links": []}


def test_no_local_path_reaches_the_file():
    assert "/home/" not in modelo.texto(modelo.normalizar(_raw()))


def test_relations_get_an_id_from_their_content():
    relations = modelo.normalizar(_raw())["relations"]
    assert [r["id"] for r in relations] == ["p -> s.s2", "p -> s.s2: usa", "p -> s: lê"]
    assert relations[2] == {
        "id": "p -> s: lê", "source": "p", "target": "s", "title": "lê", "kind": "leitura",
        "description": "seta", "technology": "HTTPS", "tags": ["novo"], "metadata": {"bead": "df-1"},
        "links": [{"url": "https://example.com/r", "title": None}],
    }
    assert relations[0]["title"] == ""
    assert relations[1]["kind"] is None


def test_a_new_relation_renames_no_other():
    """LikeC4 renumbers the relations after one added earlier in the file; modelo.json does not."""
    before = modelo.normalizar(_raw())["relations"]
    raw = _raw()
    raw["relations"] = {"zz1": {"source": {"model": "s"}, "target": {"model": "p"}, "id": "zz1", "title": "volta"}} | {
        f"new{i}": r | {"id": f"new{i}"} for i, r in enumerate(raw["relations"].values())}
    after = modelo.normalizar(raw)["relations"]
    assert [r for r in after if r["id"] != "s -> p: volta"] == before


def test_views_keep_type_rules_and_dynamic_steps():
    views = {v["id"]: v for v in modelo.normalizar(_raw())["views"]}
    assert list(views) == ["dentro", "fluxo", "impl", "index"]
    assert views["index"] == {"id": "index", "type": "element", "title": "Índice", "description": "vista",
                              "tags": ["novo"], "links": [{"url": "https://example.com/v", "title": None}],
                              "viewOf": None, "steps": []}
    assert views["dentro"]["viewOf"] == "s"
    assert views["fluxo"]["steps"] == [{"source": "p", "target": "s", "title": "pede"},
                                       {"source": "s", "target": "p", "title": ""}]
    assert views["impl"]["type"] == "deployment"


def test_deployment_splits_nodes_and_instances():
    deployment = modelo.normalizar(_raw())["deployment"]
    assert [n["id"] for n in deployment["nodes"]] == ["prod", "prod.pc"]
    assert deployment["nodes"][1] == {"id": "prod.pc", "kind": "maquina", "title": "PC", "description": None,
                                      "technology": "Windows", "tags": [], "metadata": {"servidoPor": "prod"},
                                      "links": []}
    assert [(i["id"], i["element"], i["title"]) for i in deployment["instances"]] == [
        ("prod.pc.inst", "s.s2", "Filho no PC"), ("prod.pc.s", "s", "Sistema")]
    assert [r["id"] for r in deployment["relations"]] == ["prod.pc.inst -> prod.pc: copia"]


def test_specification_lists_the_names():
    assert modelo.normalizar(_raw())["specification"] == {
        "elements": ["pessoa", "sistema"], "tags": ["muda", "novo"],
        "relationships": ["leitura"], "deployments": ["ambiente", "maquina"]}


def test_every_record_has_every_key():
    m = modelo.normalizar({"elements": {"a": {"id": "a", "kind": "k"}}, "relations": {
        "r": {"id": "r", "source": {"model": "a"}, "target": {"model": "a"}}}, "views": {
        "v": {"_type": "element", "id": "v"}}})
    assert set(m) == {"likec4", "specification", "elements", "relations", "views", "deployment"}
    assert m["relations"][0]["id"] == "a -> a"
    assert m["relations"][0]["title"] == ""
    assert set(m["elements"][0]) == {"id", "kind", "title", "description", "technology", "tags", "metadata", "links"}
    assert m["deployment"] == {"nodes": [], "instances": [], "relations": []}


def test_an_empty_export_is_an_empty_model():
    assert modelo.normalizar({}) == {
        "likec4": modelo.LIKEC4,
        "specification": {"elements": [], "tags": [], "relationships": [], "deployments": []},
        "elements": [], "relations": [], "views": [],
        "deployment": {"nodes": [], "instances": [], "relations": []}}


def test_the_text_keeps_utf8_as_is():
    assert '"title": "Índice"' in modelo.texto(modelo.normalizar(_raw()))


def test_the_text_does_not_depend_on_the_export_order():
    raw = _raw()
    reordered = {k: dict(reversed(v.items())) if isinstance(v, dict) else v for k, v in reversed(raw.items())}
    reordered["deployments"] = {k: dict(reversed(v.items())) for k, v in raw["deployments"].items()}
    assert modelo.texto(modelo.normalizar(reordered)) == modelo.texto(modelo.normalizar(raw))


@pytest.mark.parametrize(("where", "value", "message"), [
    (("relations", "14yrxbb", "source"), {"model": "p", "element": "x"},
     "unknown LikeC4 relation end: {'model': 'p', 'element': 'x'}"),
    (("relations", "14yrxbb", "target"), "s", "unknown LikeC4 relation end: 's'"),
    (("elements", "p", "description"), {"txt": "a", "md": "b"}, "unknown LikeC4 text: {'txt': 'a', 'md': 'b'}"),
    (("views", "impl", "_type"), "sequence", "unknown LikeC4 view type: 'sequence'"),
])
def test_an_unknown_shape_fails_loudly(where, value, message):
    """A new LikeC4 shape stops gerar instead of writing a guess."""
    raw = _raw()
    target = raw
    for key in where[:-1]:
        target = target[key]
    target[where[-1]] = value
    with pytest.raises(ValueError) as refused:
        modelo.normalizar(raw)
    assert str(refused.value) == message


def test_gerar_writes_what_load_reads_and_conferir_sees_a_stale_file(tmp_path, monkeypatch, capsys):
    raw = _raw()

    def exportar(fonte):
        assert fonte == tmp_path
        return copy.deepcopy(raw)

    monkeypatch.setattr(modelo, "exportar", exportar)
    saida = tmp_path / "modelo.json"
    monkeypatch.setattr(modelo, "FONTE", tmp_path)
    monkeypatch.setattr(modelo, "SAIDA", saida)
    assert modelo.main(["gerar"]) == 0
    assert saida.read_text(encoding="utf-8") == modelo.texto(modelo.normalizar(raw))
    saida.unlink()
    modelo.gerar(tmp_path, saida)
    assert modelo.load(saida) == json.loads(modelo.texto(modelo.normalizar(raw)))
    assert modelo.conferir(tmp_path, saida) == []
    capsys.readouterr()
    assert modelo.main(["conferir"]) == 0
    assert capsys.readouterr().out == f"{saida} is the export of {tmp_path}\n"
    raw["elements"]["s"]["title"] = "Sistema novo"
    diff = modelo.conferir(tmp_path, saida)
    assert diff[:2] == [f"--- {saida}", "+++ export of the .c4"]
    assert '-      "title": "Sistema"' in diff
    assert '+      "title": "Sistema novo"' in diff
    assert modelo.main(["conferir"]) == 1
    assert capsys.readouterr().out == (
        f"{saida} is stale: run python3 scripts/arquitetura/modelo.py gerar and commit it\n" + "\n".join(diff) + "\n")
    saida.unlink()
    diff = modelo.conferir(tmp_path, saida)
    assert diff[:3] == [f"--- {saida}", "+++ export of the .c4", f"@@ -0,0 +1,{len(diff) - 3} @@"]
    assert all(line.startswith("+") for line in diff[3:])
    assert modelo.main(["conferir"]) == 1


def test_the_committed_model_loads_by_default():
    m = modelo.load()
    assert m["likec4"] == modelo.LIKEC4
    assert {"elements", "relations", "views", "deployment", "specification"} <= set(m)
    ids = {e["id"] for e in m["elements"]}
    assert all(r["source"] in ids for r in m["relations"])
    assert all(r["target"] in ids for r in m["relations"])


def test_the_readme_docs_commands_the_ci_job_and_modelo_use_the_same_likec4():
    """ci.yml sets the version once, in the job's LIKEC4_VERSION; modelo.py pins it as a constant;
    every command of arquitetura/README.md and docs/commands.md runs it, and their validate takes
    the arguments the CI step runs."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = yamlio.load(ci)["jobs"]["arquitetura"]
    assert job["env"]["LIKEC4_VERSION"] == modelo.LIKEC4
    assert ci.count(modelo.LIKEC4) == 1
    assert re.findall(r"likec4@\S*", ci, re.IGNORECASE) == ['likec4@$LIKEC4_VERSION"']
    (step,) = [s for s in job["steps"] if s.get("name") == "Validate the model"]
    (ci_args,) = re.findall(r'likec4@\$LIKEC4_VERSION"?\s+(.*?)\s+2>&1', step["run"])
    assert ci_args.split()[0] == "validate"
    for doc in (ROOT / "arquitetura" / "README.md", ROOT / "docs" / "commands.md"):
        text = doc.read_text(encoding="utf-8")
        pins = re.findall(r"likec4@\S*", text, re.IGNORECASE)
        assert pins, doc
        assert set(pins) == {f"likec4@{modelo.LIKEC4}"}, doc
        validates = re.findall(r"likec4(@\S+)?\s+(validate[^\n#]*)", text, re.IGNORECASE)
        assert validates, doc
        for pin, args in validates:
            assert pin == f"@{modelo.LIKEC4}", (doc, pin, args)
            assert args.split() == ci_args.split(), (doc, args)


def test_two_arrows_with_the_same_title_stop_gerar():
    """Numbering repeats would let an id collide with a real title, and editing one arrow's tag
    could swap the numbers: the PR diff would report arrows that did not change."""
    raw = _raw()
    raw["relations"]["1exzn7k"]["title"] = "usa"
    raw["relations"]["copia"] = raw["relations"]["10wqd61"] | {"id": "copia"}
    with pytest.raises(ValueError, match=r"same source, target and title: p -> s\.s2: usa, p -> s: lê; give them"):
        modelo.normalizar(raw)


@pytest.mark.parametrize("acao", ["gerar", "conferir"])
def test_the_cli_shows_a_refused_model_as_a_message(tmp_path, monkeypatch, acao):
    raw = _raw()
    raw["relations"]["1exzn7k"]["title"] = "usa"
    monkeypatch.setattr(modelo, "exportar", lambda fonte: raw)
    saida = tmp_path / "modelo.json"
    monkeypatch.setattr(modelo, "SAIDA", saida)
    with pytest.raises(SystemExit, match="give them distinct titles") as stopped:
        modelo.main([acao])
    assert isinstance(stopped.value.code, str)
    assert not saida.exists()


FAKE_NPX = """#!/bin/sh
echo "$* NO_COLOR=$NO_COLOR" >> "$NPX_LOG"
if [ "$3" = validate ]; then echo "out: 1 warning"; echo "err: bad" >&2; exit "$NPX_VALIDATE_EXIT"; fi
echo '{"elements": {"a": {"id": "a", "kind": "k", "title": "Índice"}}}' > "$7"
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the fake npx is a sh script")
@pytest.mark.parametrize(("validate_exit", "calls"), [(0, 2), (1, 1)])
def test_exportar_validates_before_it_exports(tmp_path, monkeypatch, validate_exit, calls):
    """The export of an invalid model exits 0 and leaves out what it could not read; a warning
    fails the validate (df-il2f)."""
    npx = tmp_path / "bin" / "npx"
    npx.parent.mkdir()
    npx.write_text(FAKE_NPX, encoding="utf-8")
    npx.chmod(0o755)
    log = tmp_path / "calls"
    monkeypatch.setenv("PATH", f"{npx.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("NPX_LOG", str(log))
    monkeypatch.setenv("NPX_VALIDATE_EXIT", str(validate_exit))
    monkeypatch.delenv("NO_COLOR", raising=False)
    if validate_exit:
        with pytest.raises(SystemExit) as stopped:
            modelo.exportar(tmp_path)
        assert stopped.value.code == "likec4 validate failed (exit 1):\nout: 1 warning\nerr: bad\n"
    else:
        assert modelo.exportar(tmp_path) == {"elements": {"a": {"id": "a", "kind": "k", "title": "Índice"}}}
    pin = f"--yes likec4@{modelo.LIKEC4}"
    expected = [f"{pin} validate --max-warnings 0 {tmp_path} NO_COLOR=1",
                f"{pin} export json --skip-layout -o <saida> {tmp_path} NO_COLOR=1"][:calls]
    calls_made = log.read_text(encoding="utf-8").splitlines()
    assert [re.sub(r" -o \S+ ", " -o <saida> ", call) for call in calls_made] == expected


def test_exportar_without_npx_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SystemExit) as stopped:
        modelo.exportar(tmp_path)
    assert stopped.value.code == "npx not found: install Node (arquitetura/README.md)"


@pytest.mark.parametrize("argv", [["conferir", "--fonte", "x"], ["conferir", "--saida", "x"], ["exportar"]])
def test_the_cli_takes_no_path(argv, monkeypatch):
    """A path from the command line would reach the npx call and the file write (Sonar S8705,
    S8707): the CLI only reads arquitetura/ and writes arquitetura/modelo.json. Its only actions
    are gerar and conferir."""
    monkeypatch.setattr(modelo, "exportar", lambda fonte: pytest.fail("the CLI ran an export"))
    with pytest.raises(SystemExit) as stopped:
        modelo.main(argv)
    assert stopped.value.code == 2


def test_the_cli_help_is_the_first_line_of_the_docstring(capsys):
    with pytest.raises(SystemExit) as stopped:
        modelo.main(["--help"])
    assert stopped.value.code == 0
    words = " ".join(capsys.readouterr().out.split())  # argparse wraps the text at the terminal width
    assert " ".join(modelo.__doc__.splitlines()[0].split()) in words
