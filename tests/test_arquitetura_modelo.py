"""scripts/arquitetura/modelo.py: arquitetura/modelo.json, the small sorted JSON of the LikeC4 model
(df-8x0m.23.43). The raw dicts below have the shapes `likec4@1.61.1 export json --skip-layout` gives;
the CI job `arquitetura` runs the real export (`conferir`)."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

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
            "1exzn7k": {"source": {"model": "p"}, "target": {"model": "s.s2"}, "id": "1exzn7k", "title": "usa"},
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
    assert [r["id"] for r in relations] == ["p -> s.s2: usa", "p -> s.s2: usa (2)", "p -> s: lê"]
    assert relations[2] == {
        "id": "p -> s: lê", "source": "p", "target": "s", "title": "lê", "kind": "leitura",
        "description": "seta", "technology": "HTTPS", "tags": ["novo"], "metadata": {"bead": "df-1"},
        "links": [{"url": "https://example.com/r", "title": None}],
    }
    assert relations[0]["kind"] is None


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
    assert m["relations"][0]["id"] == "a -> a: "
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
    modelo.gerar(tmp_path, saida)
    assert modelo.load(saida) == json.loads(modelo.texto(modelo.normalizar(raw)))
    assert modelo.conferir(tmp_path, saida) == []
    assert modelo.main(["conferir", "--fonte", str(tmp_path), "--saida", str(saida)]) == 0
    raw["elements"]["s"]["title"] = "Sistema novo"
    diff = modelo.conferir(tmp_path, saida)
    assert '-      "title": "Sistema"' in diff
    assert '+      "title": "Sistema novo"' in diff
    assert modelo.main(["conferir", "--fonte", str(tmp_path), "--saida", str(saida)]) == 1
    saida.unlink()
    assert modelo.main(["conferir", "--fonte", str(tmp_path), "--saida", str(saida)]) == 1


def test_the_committed_model_loads_by_default():
    m = modelo.load()
    assert m["likec4"] == modelo.LIKEC4
    assert {"elements", "relations", "views", "deployment", "specification"} <= set(m)
    assert all(r["source"] in {e["id"] for e in m["elements"]} for r in m["relations"])


def test_the_readme_and_the_ci_job_use_the_same_likec4():
    pin = f"likec4@{modelo.LIKEC4}"
    assert pin in (ROOT / "arquitetura" / "README.md").read_text(encoding="utf-8")
    assert pin in (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
