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
    [element("casa.app", ["muda"], {"bead": "df-a.1"}),
     element("fonte", ["novo"], {"bead": ["df-a.2", "df-b"]}),
     element("casa.velho", ["sai"], {"bead": "df-a.3"}),
     element("casa.igual"),
     element("casa.outro", ["emProd"])],
    [relation("casa.app", "casa.igual", ["muda"], {"bead": "df-a.1"}),
     relation("casa.igual", "fonte")],
)


def test_a_fully_marked_model_passes():
    assert marcas.problems(GOOD) == []


def test_a_marked_element_without_a_bead_fails():
    found = marcas.problems(model([element("casa.app", ["novo"])]))
    assert len(found) == 1
    assert "casa.app" in found[0]
    assert "sem metadata" in found[0]


def test_a_marked_relation_without_a_bead_fails_and_is_named_by_its_ends():
    found = marcas.problems(model(relations=[relation("casa.a", "casa.b", ["sai"], title="grava")]))
    assert len(found) == 1
    assert "casa.a -> casa.b: grava" in found[0]


def test_two_of_the_three_tags_fail():
    found = marcas.problems(model([element("x", ["novo", "muda"], {"bead": "df-a"})]))
    assert len(found) == 1
    assert "#muda" in found[0]
    assert "#novo" in found[0]


@pytest.mark.parametrize("bead", ["", "df-", "DF-1", "df-1 ", "df-1\n", "gf-1", "df-a..1", "df-a.x", "#df-a"])
def test_a_malformed_bead_id_fails(bead):
    found = marcas.problems(model([element("x", ["muda"], {"bead": bead})]))
    assert len(found) == 1
    assert "malformado" in found[0]


@pytest.mark.parametrize("value", [[], ["df-a", 3], {"id": "df-a"}, 7])
def test_a_bead_value_that_is_not_ids_fails(value):
    assert len(marcas.problems(model([element("x", ["muda"], {"bead": value})]))) == 1


def test_a_bead_without_a_tag_fails():
    found = marcas.problems(model([element("x", [], {"bead": "df-a"})]))
    assert len(found) == 1
    assert "sem marca" in found[0]


def test_a_missing_section_counts_as_empty():
    assert marcas.problems({}) == []


IDS = st.from_regex(r"df-[a-z0-9]{1,6}(\.[0-9]{1,3}){0,3}", fullmatch=True)


@given(tags=st.lists(st.sampled_from(marcas.TAGS), unique=True), beads=st.lists(IDS, max_size=3))
def test_an_item_passes_exactly_when_it_has_one_tag_and_some_well_formed_bead(tags, beads):
    metadata = {"bead": beads} if beads else {}
    found = marcas.problems(model([element("x", tags, metadata)]))
    assert (found == []) == ((len(tags) == 1 and bool(beads)) or (not tags and not beads))


def test_marked_beads_maps_each_bead_to_what_it_marks():
    assert marcas.marked_beads(GOOD) == {
        "df-a.1": ["casa.app", "casa.app -> casa.igual: usa"],
        "df-a.2": ["fonte"], "df-b": ["fonte"], "df-a.3": ["casa.velho"]}


def test_bd_problems_name_closed_and_unknown_beads():
    status = {"df-a.1": "open", "df-a.2": "in_progress", "df-b": "closed", "df-a.3": None}.get
    found = marcas.bd_problems(GOOD, status)
    assert len(found) == 2
    assert "df-b" in found[0]
    assert "fechado" in found[0]
    assert "fonte" in found[0]
    assert "df-a.3" in found[1]
    assert "não existe" in found[1]


def test_main_reads_the_model_file_and_exits_1_with_the_problems(tmp_path, capsys):
    path = tmp_path / "modelo.json"
    path.write_text(json.dumps(model([element("x", ["novo"])])), encoding="utf-8")
    assert marcas.main([str(path)]) == 1
    assert "x" in capsys.readouterr().out
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    assert marcas.main([str(path)]) == 0


def test_main_with_bd_asks_bd_for_each_bead(tmp_path, monkeypatch, capsys):
    path = tmp_path / "modelo.json"
    path.write_text(json.dumps(GOOD), encoding="utf-8")
    monkeypatch.setattr(marcas, "bd_status", lambda bead: "closed" if bead == "df-b" else "open")
    assert marcas.main(["--bd", str(path)]) == 1
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
    path.write_text(json.dumps(model([element("x", ["novo"]), element("y", ["muda"], {"bead": "df-a"})])),
                    encoding="utf-8")
    monkeypatch.setattr(marcas.subprocess, "run", fake_run(1, "", "database is locked"))
    assert marcas.main(["--bd", str(path)]) == 2
    out, err = capsys.readouterr()
    assert "x: #novo sem metadata" in out
    assert "database is locked" in err


def test_the_versioned_model_is_fully_marked():
    """The real model: every mark of arquitetura/ carries a well-formed bead (CI's half)."""
    path = ROOT / "arquitetura" / "modelo.json"
    if not path.exists():
        pytest.skip("arquitetura/modelo.json comes with df-8x0m.23.43")
    assert marcas.problems(json.loads(path.read_text(encoding="utf-8"))) == []
