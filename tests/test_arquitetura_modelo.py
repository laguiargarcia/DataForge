"""scripts/arquitetura/modelo.py: arquitetura/modelo.json, the small sorted JSON of the LikeC4 model
(df-8x0m.23.43). The raw dicts below have the shapes `likec4 export json --skip-layout` gives (at LIKEC4);
the CI job `arquitetura` runs the real export (`conferir`)."""

import copy
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

from dataforge import yamlio

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("modelo", ROOT / "scripts" / "arquitetura" / "modelo.py")
modelo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modelo)


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
    assert deployment["nodes"][1]["metadata"] == {"servidoPor": "prod"}
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


def test_the_text_does_not_depend_on_the_export_order():
    raw = _raw()
    reordered = {k: dict(reversed(v.items())) if isinstance(v, dict) else v for k, v in reversed(raw.items())}
    reordered["deployments"] = {k: dict(reversed(v.items())) for k, v in raw["deployments"].items()}
    assert modelo.texto(modelo.normalizar(reordered)) == modelo.texto(modelo.normalizar(raw))


@pytest.mark.parametrize(("where", "value"), [
    (("relations", "14yrxbb", "source"), {"model": "p", "element": "x"}),
    (("relations", "14yrxbb", "target"), "s"),
    (("elements", "p", "description"), {"txt": "a", "md": "b"}),
    (("views", "impl", "_type"), "sequence"),
])
def test_an_unknown_shape_fails_loudly(where, value):
    """A new LikeC4 shape stops gerar instead of writing a guess."""
    raw = _raw()
    target = raw
    for key in where[:-1]:
        target = target[key]
    target[where[-1]] = value
    with pytest.raises(ValueError):
        modelo.normalizar(raw)


def test_gerar_writes_what_load_reads_and_conferir_sees_a_stale_file(tmp_path, monkeypatch):
    raw = _raw()
    monkeypatch.setattr(modelo, "exportar", lambda fonte: copy.deepcopy(raw))
    saida = tmp_path / "modelo.json"
    monkeypatch.setattr(modelo, "FONTE", tmp_path)
    monkeypatch.setattr(modelo, "SAIDA", saida)
    modelo.gerar(tmp_path, saida)
    assert modelo.load(saida) == json.loads(modelo.texto(modelo.normalizar(raw)))
    assert modelo.conferir(tmp_path, saida) == []
    assert modelo.main(["conferir"]) == 0
    raw["elements"]["s"]["title"] = "Sistema novo"
    diff = modelo.conferir(tmp_path, saida)
    assert '-      "title": "Sistema"' in diff
    assert '+      "title": "Sistema novo"' in diff
    assert modelo.main(["conferir"]) == 1
    saida.unlink()
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
    with pytest.raises(ValueError, match=r"p -> s\.s2: usa; give them distinct titles"):
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
echo "$2 $3 $4 $5" >> "$NPX_LOG"
if [ "$3" = validate ]; then exit "$NPX_VALIDATE_EXIT"; fi
echo '{"elements": {"a": {"id": "a", "kind": "k"}}}' > "$7"
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
    if validate_exit:
        with pytest.raises(SystemExit, match="likec4 validate failed"):
            modelo.exportar(tmp_path)
    else:
        assert modelo.exportar(tmp_path) == {"elements": {"a": {"id": "a", "kind": "k"}}}
    pin = f"likec4@{modelo.LIKEC4}"
    expected = [f"{pin} validate --max-warnings 0", f"{pin} export json --skip-layout"][:calls]
    assert log.read_text(encoding="utf-8").splitlines() == expected


@pytest.mark.parametrize("opcao", ["--fonte", "--saida"])
def test_the_cli_takes_no_path(opcao):
    """A path from the command line would reach the npx call and the file write (Sonar S8705,
    S8707): the CLI only reads arquitetura/ and writes arquitetura/modelo.json."""
    with pytest.raises(SystemExit) as stopped:
        modelo.main(["conferir", opcao, "x"])
    assert stopped.value.code == 2


@pytest.mark.parametrize(("id_", "top", "hoje"), [
    ("casaHoje", "casaHoje", True), ("casaHoje.servidor.banco", "casaHoje", True),
    ("casaHoje.appAlvo", "casaHoje", True), ("casaAlvo.velhoHoje", "casaAlvo", False),
    ("pluggyAlvo", "pluggyAlvo", False), ("casa", "casa", False), ("casaHojeX.a", "casaHojeX", False)])
def test_the_model_of_an_id_is_its_top_segments_suffix(id_, top, hoje):
    assert modelo.sistema(id_) == top
    assert modelo.e_hoje(id_) is hoje


def _vista(label, *relations, source="p", target="s", kind=None) -> dict:
    """_raw() whose element view `index` draws one edge `source -> target` over `relations`, each
    (id, source, target, title, kind); the export gives an edge the kind of one of its relations."""
    raw = _raw()
    for rid, src, tgt, title, rkind in relations:
        raw["relations"][rid] = {"id": rid, "source": {"model": src}, "target": {"model": tgt}, "title": title,
                                 "kind": rkind}
    raw["views"]["index"]["edges"] = [{"id": "e1", "source": source, "target": target, "label": label,
                                       "kind": kind, "relations": [r[0] for r in relations]}]
    return raw


ABRE = ("r1", "p", "s.s2", "abre", None)
LE = ("r2", "p", "s.s2", "lê", None)


def test_an_edge_drawn_as_dots_fails_conferir_with_its_view_and_pair(tmp_path, monkeypatch, capsys):
    """Rule 3 of arquitetura/README.md: '[...]' tells the reader nothing (df-8x0m.23.47.12)."""
    raw = _vista("[...]", ABRE, LE)
    monkeypatch.setattr(modelo, "exportar", lambda fonte: raw)
    saida = tmp_path / "modelo.json"
    saida.write_text(modelo.texto(modelo.normalizar(raw)), encoding="utf-8")
    monkeypatch.setattr(modelo, "SAIDA", saida)
    assert modelo.main(["conferir"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "view index: p -> s is drawn as '[...]': give it one title in the view "
        "(include p -> s with { title '...' })"]


@pytest.mark.parametrize(("raw", "shown"), [
    (_vista("abre", ABRE, LE), "abre"),
    (_vista("abre", ABRE, ("r3", "p", "s", "", None)), "abre"),  # an untitled direct arrow gives no title
])
def test_an_edge_showing_one_of_several_joined_titles_is_ambiguous(raw, shown):
    """LikeC4 1.61.1 sometimes shows one joined title and hides the rest, with no '[...]'."""
    assert modelo.arestas_ambiguas(raw) == [
        f"view index: p -> s joins 2 relations and shows only {shown!r}: give it a title in the view "
        "(include p -> s with { title '...' }) or draw an arrow straight between the two"]


@pytest.mark.parametrize("raw", [
    _vista("Título da vista", ABRE, LE),  # the view gives the title (`with { title }`)
    _vista("junta", ABRE, ("r3", "p", "s", "junta", None)),  # a direct arrow gives it
    _vista("abre", ABRE, ("r3", "p", "s.s3", "abre", None)),  # no direct arrow, one title for all
    _vista("abre", ABRE),
])
def test_an_edge_that_reads_at_its_word_passes(raw):
    assert modelo.arestas_ambiguas(raw) == []


@pytest.mark.parametrize("label", ["lê", "Título da vista"])
def test_an_edge_joining_kinds_is_ambiguous_even_with_a_title(label):
    """A leitura joined with plain arrows comes out leitura, dotted; neither a direct arrow nor a
    view title changes the kind."""
    raw = _vista(label, ("r3", "p", "s", "lê", None), ("r4", "p", "s.s2", "copia", "leitura"), kind="leitura")
    assert modelo.arestas_ambiguas(raw) == [
        "view index: p -> s joins relations of kinds (none), leitura and is drawn as leitura: "
        "give the arrows behind it one kind"]


def test_dynamic_steps_are_not_checked_and_an_unknown_relation_fails_loudly():
    raw = _raw()
    raw["views"]["fluxo"]["edges"][0]["label"] = "[...]"
    assert modelo.arestas_ambiguas(raw) == []
    raw["views"]["index"]["edges"][0]["relations"] = ["nenhuma"]
    with pytest.raises(ValueError, match="view index: edge e1 names an unknown relation 'nenhuma'"):
        modelo.arestas_ambiguas(raw)
