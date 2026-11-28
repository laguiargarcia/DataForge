"""scripts/arquitetura/marcas.py: every element or relation of the target that differs from today
carries one of the tags novo, muda, sai and the id of an open bead (df-8x0m.23.37). The format and
presence are checked from the model alone (CI); whether the bead is open needs bd (--bd, local)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "arquitetura" / "marcas.py"
_spec = importlib.util.spec_from_file_location("arquitetura_marcas", SCRIPT)
marcas = importlib.util.module_from_spec(_spec)
sys.modules["arquitetura_marcas"] = marcas
_spec.loader.exec_module(marcas)


def element(fqn, tags=(), metadata=None):
    return {"id": fqn, "kind": "conteiner", "title": fqn, "tags": list(tags), "metadata": metadata or {}}


def relation(source, target, tags=(), metadata=None, title="usa"):
    return {"id": f"{source} -> {target}: {title}", "source": source, "target": target, "title": title,
            "tags": list(tags), "metadata": metadata or {}}


def model(elements=(), relations=()):
    return {"elements": list(elements), "relations": list(relations)}


GOOD = model(
    [element("casaAlvo.app", ["muda"], {"bead": "df-a.1"}),
     element("fonteAlvo", ["novo"], {"bead": ["df-a.2", "df-b"]}),
     element("casaAlvo.velho", ["sai"], {"bead": "df-a.3"}),
     element("casaAlvo.igual"),
     element("casaAlvo.outro", ["emProd"])],
    [relation("casaAlvo.app", "casaAlvo.igual", ["muda"], {"bead": "df-a.1"}),
     relation("casaAlvo.igual", "fonteAlvo")],
)


def test_a_fully_marked_model_passes():
    assert marcas.problems(GOOD) == []


def test_a_marked_element_without_a_bead_fails():
    found = marcas.problems(model([element("casaAlvo.app", ["novo"])]))
    assert len(found) == 1
    assert "casaAlvo.app" in found[0]
    assert "sem metadata" in found[0]


def test_a_marked_relation_without_a_bead_fails_and_is_named_by_its_ends():
    found = marcas.problems(model(relations=[relation("casa.a", "casa.b", ["sai"], title="grava")]))
    assert len(found) == 1
    assert "casa.a -> casa.b: grava" in found[0]


def test_two_of_the_three_tags_fail():
    found = marcas.problems(model([element("xAlvo", ["novo", "muda"], {"bead": "df-a"})]))
    assert len(found) == 1
    assert "#muda" in found[0]
    assert "#novo" in found[0]


@pytest.mark.parametrize("bead", ["", "df-", "DF-1", "df-1 ", "df-1\n", "gf-1", "df-a..1", "df-a.x", "#df-a"])
def test_a_malformed_bead_id_fails(bead):
    found = marcas.problems(model([element("xAlvo", ["muda"], {"bead": bead})]))
    assert len(found) == 1
    assert "malformado" in found[0]


@pytest.mark.parametrize("value", [[], ["df-a", 3], {"id": "df-a"}, 7])
def test_a_bead_value_that_is_not_ids_fails(value):
    assert len(marcas.problems(model([element("xAlvo", ["muda"], {"bead": value})]))) == 1


def test_a_bead_without_a_tag_fails():
    found = marcas.problems(model([element("xAlvo", [], {"bead": "df-a"})]))
    assert len(found) == 1
    assert "sem marca" in found[0]


def test_a_missing_section_counts_as_empty():
    assert marcas.problems({}) == []


@pytest.mark.parametrize("tag", marcas.TAGS)
@pytest.mark.parametrize("fqn", ["casaHoje", "casaHoje.servidorHoje", "casaHoje.servidor.banco"])
def test_a_marked_element_of_today_fails(tag, fqn):
    found = marcas.problems(model([element("casaHoje"), element(fqn, [tag], {"bead": "df-a"})]))
    assert len(found) == 1
    assert found[0].startswith(f"{fqn}: #{tag} no modelo de hoje")


@pytest.mark.parametrize("tag", marcas.TAGS)
@pytest.mark.parametrize(("source", "target"), [
    ("casaHoje.app", "casaHoje.servidor"), ("donoHoje", "casaAlvo.app"), ("casaAlvo.app", "pluggyHoje")])
def test_a_marked_relation_with_an_end_in_today_fails(tag, source, target):
    found = marcas.problems(model(relations=[relation(source, target, [tag], {"bead": "df-a"})]))
    assert len(found) == 1
    assert found[0].startswith(f"{source} -> {target}: usa: #{tag} no modelo de hoje")


def test_a_marked_item_of_today_without_a_bead_names_both_problems():
    found = marcas.problems(model([element("donoHoje", ["novo"])]))
    assert len(found) == 2


def test_unmarked_items_of_today_pass():
    today = model([element("casaHoje"), element("casaHoje.app", ["leitura"])],
                  [relation("donoHoje", "casaHoje.app")])
    assert marcas.problems(today) == []


@pytest.mark.parametrize("top", ["casa", "fonteNova", "casaHojeX", "alvo", "hoje", "casa_Alvo2"])
def test_a_top_level_element_named_neither_today_nor_target_fails(top):
    found = marcas.problems(model([element(top), element(f"{top}.filho")]))
    assert len(found) == 1
    assert repr(top) in found[0]
    assert "Hoje" in found[0]
    assert "Alvo" in found[0]
    assert "(hoje.c4)" not in found[0]  # the files that extend casaHoje are today's model too


def test_the_suffix_counts_only_on_the_top_level_segment():
    """A child named ...Hoje under a target system is the target's; a child ...Alvo under today is today's."""
    assert marcas.problems(model([element("casaAlvo"), element("casaAlvo.velhoHoje", ["sai"], {"bead": "df-a"})])) == []
    found = marcas.problems(model([element("casaHoje"), element("casaHoje.appAlvo", ["muda"], {"bead": "df-a"})]))
    assert len(found) == 1
    assert "casaHoje.appAlvo" in found[0]


def instance(element_, tags=(), metadata=None, id_="prod.servidor"):
    return {"id": id_, "element": element_, "title": id_, "tags": list(tags), "metadata": metadata or {}}


@pytest.mark.parametrize("tag", marcas.TAGS)
@pytest.mark.parametrize("element_", ["casaHoje", "casaHoje.servidorHoje", "pluggyHoje.api"])
def test_a_marked_deployment_instance_of_a_today_element_fails(tag, element_):
    found = marcas.problems({"deployment": {"instances": [instance(element_, [tag], {"bead": "df-a"})]}})
    assert len(found) == 1
    assert found[0].startswith(f"prod.servidor (instância de {element_}): #{tag} no modelo de hoje")


def test_a_marked_deployment_instance_of_a_target_element_passes():
    deployed = {"instances": [instance("casaAlvo.servidorAlvo", ["novo"], {"bead": "df-a"})]}
    assert marcas.problems({"deployment": deployed}) == []


def test_deployment_nodes_and_relations_stay_outside_the_today_rule():
    """Their ids are deployment ids, not model ids: no suffix tells the model."""
    node = {"id": "pcHoje", "kind": "maquina", "tags": ["novo"], "metadata": {"bead": "df-a"}}
    arrow = relation("pcHoje.a", "pcHoje.b", ["muda"], {"bead": "df-a"})
    assert marcas.problems({"deployment": {"nodes": [node], "relations": [arrow]}}) == []


TOPS = st.from_regex(r"[a-z][a-zA-Z]{0,8}(Hoje|Alvo)?", fullmatch=True)


@given(top=TOPS, child=st.booleans(), tag=st.sampled_from(marcas.TAGS))
def test_a_well_formed_mark_passes_exactly_on_a_target_element(top, child, tag):
    mark = ([tag], {"bead": "df-a"})
    elements = [element(top), element(f"{top}.filho", *mark)] if child else [element(top, *mark)]
    assert (marcas.problems(model(elements)) == []) == top.endswith("Alvo")


@given(source=TOPS, target=TOPS, tag=st.sampled_from(marcas.TAGS))
def test_a_well_formed_marked_relation_passes_exactly_when_no_end_is_today(source, target, tag):
    found = marcas.problems(model(relations=[relation(f"{source}.a", f"{target}.b", [tag], {"bead": "df-a"})]))
    assert (found == []) == (not source.endswith("Hoje") and not target.endswith("Hoje"))


IDS = st.from_regex(r"df-[a-z0-9]{1,6}(\.[0-9]{1,3}){0,3}", fullmatch=True)


@given(tags=st.lists(st.sampled_from(marcas.TAGS), unique=True), beads=st.lists(IDS, max_size=3))
def test_an_item_passes_exactly_when_it_has_one_tag_and_some_well_formed_bead(tags, beads):
    metadata = {"bead": beads} if beads else {}
    found = marcas.problems(model([element("xAlvo", tags, metadata)]))
    assert (found == []) == ((len(tags) == 1 and bool(beads)) or (not tags and not beads))


def test_marked_beads_maps_each_bead_to_what_it_marks():
    assert marcas.marked_beads(GOOD) == {
        "df-a.1": ["casaAlvo.app", "casaAlvo.app -> casaAlvo.igual: usa"],
        "df-a.2": ["fonteAlvo"], "df-b": ["fonteAlvo"], "df-a.3": ["casaAlvo.velho"]}


def test_bd_problems_name_closed_and_unknown_beads():
    status = {"df-a.1": "open", "df-a.2": "in_progress", "df-b": "closed", "df-a.3": None}.get
    found = marcas.bd_problems(GOOD, status)
    assert len(found) == 2
    assert "df-b" in found[0]
    assert "fechado" in found[0]
    assert "fonteAlvo" in found[0]
    assert "df-a.3" in found[1]
    assert "não existe" in found[1]


def test_main_reads_the_model_file_and_exits_1_with_the_problems(tmp_path, monkeypatch, capsys):
    path = tmp_path / "modelo.json"
    monkeypatch.setattr(marcas.modelo, "SAIDA", path)
    path.write_text(json.dumps(model([element("xAlvo", ["novo"])])), encoding="utf-8")
    assert marcas.main([]) == 1
    assert "xAlvo" in capsys.readouterr().out
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    assert marcas.main([]) == 0


@pytest.mark.parametrize("argv", [["arquitetura/modelo.json"], ["--bd", "x.json"], ["--json"]])
def test_main_refuses_any_argument_but_bd(argv, monkeypatch, capsys):
    monkeypatch.setattr(marcas.modelo, "load", lambda *_a: pytest.fail("read the model"))
    assert marcas.main(argv) == 2
    assert "argumento desconhecido" in capsys.readouterr().err


def test_main_with_bd_asks_bd_for_each_bead(tmp_path, monkeypatch, capsys):
    path = tmp_path / "modelo.json"
    monkeypatch.setattr(marcas.modelo, "SAIDA", path)
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    monkeypatch.setattr(marcas, "bd_status", lambda bead: "closed" if bead == "df-b" else "open")
    assert marcas.main(["--bd"]) == 1
    assert "df-b" in capsys.readouterr().out


def fake_run(returncode=0, stdout="", stderr="", error=None):
    def run(*_args, **_kwargs):
        if error:
            raise error
        return subprocess.CompletedProcess([], returncode, stdout, stderr)
    return run


def test_bd_status_reads_the_status(monkeypatch):
    monkeypatch.setattr(marcas.subprocess, "run", fake_run(stdout='[{"id": "df-a", "status": "closed"}]'))
    assert marcas.bd_status("df-a") == "closed"


def test_bd_status_of_an_unknown_bead_is_none(monkeypatch):
    out = '{"error": "no issues found matching the provided IDs"}'
    err = 'Error fetching df-x: no issue found matching "df-x"'
    monkeypatch.setattr(marcas.subprocess, "run", fake_run(1, out, err))
    assert marcas.bd_status("df-x") is None


@pytest.mark.parametrize("run", [
    fake_run(1, "", "database is locked"),
    fake_run(0, "not json"),
    fake_run(0, "[]"),
    fake_run(error=FileNotFoundError("bd")),
    fake_run(error=subprocess.TimeoutExpired("bd", 20)),
])
def test_bd_status_raises_when_bd_fails_instead_of_calling_the_bead_missing(monkeypatch, run):
    monkeypatch.setattr(marcas.subprocess, "run", run)
    with pytest.raises(marcas.BdUnavailable):
        marcas.bd_status("df-a")


def test_main_exits_2_when_bd_fails_and_still_prints_the_format_problems(tmp_path, monkeypatch, capsys):
    path = tmp_path / "modelo.json"
    monkeypatch.setattr(marcas.modelo, "SAIDA", path)
    path.write_text(json.dumps(model([element("xAlvo", ["novo"]), element("yAlvo", ["muda"], {"bead": "df-a"})])),
                    encoding="utf-8")
    monkeypatch.setattr(marcas.subprocess, "run", fake_run(1, "", "database is locked"))
    assert marcas.main(["--bd"]) == 2
    out, err = capsys.readouterr()
    assert "xAlvo: #novo sem metadata" in out
    assert "database is locked" in err


def test_the_versioned_model_is_fully_marked():
    """The real model: every mark of arquitetura/ carries a well-formed bead (CI's half)."""
    assert marcas.problems(marcas.modelo.load()) == []


def componente(fqn):
    return {**element(fqn), "kind": "componente"}


ESPELHO = model(
    [componente("casaHoje.dfHoje.apiHoje"), componente("casaHoje.dfHoje.motorHoje"),
     componente("casaHoje.ninhoHoje.servidorHoje"), element("casaHoje.dbDataforgeHoje"),
     componente("casaAlvo.dfAlvo.apiAlvo"), componente("casaAlvo.dfAlvo.motorAlvo"),
     componente("casaAlvo.dfAlvo.rotaTabelasAlvo"), componente("casaAlvo.servidorAlvo.nucleoAlvo"),
     element("casaAlvo.historicoAlvo")],
    [relation("casaHoje.dfHoje.apiHoje", "casaHoje.dfHoje.motorHoje", metadata={"codigo": ["a.py"]}),
     relation("casaHoje.ninhoHoje.servidorHoje", "casaHoje.dbDataforgeHoje", metadata={"codigo": ["n.py"]},
              title="grava"),
     relation("casaAlvo.dfAlvo.apiAlvo", "casaAlvo.dfAlvo.motorAlvo", metadata={"codigo": ["a.py"]}),
     relation("casaAlvo.dfAlvo.rotaTabelasAlvo", "casaAlvo.dfAlvo.motorAlvo", metadata={"codigo": ["a.py"]}),
     relation("casaAlvo.servidorAlvo.nucleoAlvo", "casaAlvo.historicoAlvo", metadata={"codigo": ["n.py"]},
              title="grava"),
     relation("casaAlvo.dfAlvo.apiAlvo", "casaAlvo.historicoAlvo", ["novo"], {"bead": "df-a.1"}),
     relation("casaAlvo.dfAlvo", "casaAlvo.historicoAlvo", title="resumo do alvo")],
)


def test_every_unmarked_target_component_arrow_mirrors_one_of_today():
    """df-8x0m.23.47.20: the Hoje twin of each end (README exceptions too), same title and codigo;
    marked arrows and container arrows stay out."""
    assert marcas.problems(ESPELHO) == []


@pytest.mark.parametrize(("change", "problem"), [
    ({"title": "outro"}, "seta sem marca sem par no hoje (casaHoje.dfHoje.apiHoje -> casaHoje.dfHoje.motorHoje "
                         "com o mesmo título); ponha a marca da mudança ou copie o título e o codigo da seta de hoje"),
    ({"metadata": {"codigo": ["b.py"]}},
     "seta sem marca com codigo diferente do da seta de hoje casaHoje.dfHoje.apiHoje -> casaHoje.dfHoje.motorHoje: "
     "usa; ponha a marca da mudança ou copie o codigo de hoje"),
    ({"target": "casaAlvo.novoAlvo"}, "seta sem marca sem par no hoje (casaHoje.dfHoje.apiHoje -> None com o "
                                      "mesmo título); ponha a marca da mudança ou copie o título e o codigo da "
                                      "seta de hoje"),
])
def test_an_unmarked_target_component_arrow_out_of_step_with_today_fails(change, problem):
    broken = json.loads(json.dumps(ESPELHO))
    arrow = broken["relations"][2]
    arrow.update(change)
    assert marcas.problems(broken) == [f"{arrow['id']}: {problem}"]


def test_a_today_leaf_name_used_twice_has_no_twin():
    broken = json.loads(json.dumps(ESPELHO))
    broken["elements"].append(componente("casaHoje.outroHoje.motorHoje"))
    assert [p.split(": ", 1)[0] for p in marcas.problems(broken)] == [
        "casaAlvo.dfAlvo.apiAlvo -> casaAlvo.dfAlvo.motorAlvo", "casaAlvo.dfAlvo.rotaTabelasAlvo -> casaAlvo.dfAlvo.motorAlvo"]
