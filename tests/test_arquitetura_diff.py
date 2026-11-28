"""scripts/arquitetura/diff.py: what a PR changes in arquitetura/modelo.json, as the Markdown of its
PR comment (df-8x0m.23.38). The models below have modelo.json's layout (scripts/arquitetura/modelo.py)."""

import copy
import io
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "arquitetura" / "diff.py"
_spec = importlib.util.spec_from_file_location("arquitetura_diff", SCRIPT)
diff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diff)


def _element(id_, title, kind="conteiner", **fields):
    return {"id": id_, "kind": kind, "title": title, "description": None, "technology": None, "tags": [],
            "metadata": {}, "links": []} | fields


def _arrow(source, target, title="", **fields):
    rid = f"{source} -> {target}" + (f": {title}" if title else "")
    return {"id": rid, "source": source, "target": target, "title": title, "kind": None, "description": None,
            "technology": None, "tags": [], "metadata": {}, "links": []} | fields


def _model() -> dict:
    return {
        "likec4": "1.61.1",
        "specification": {"elements": ["conteiner", "pessoa"], "tags": ["emProd"], "relationships": [], "deployments": []},
        "elements": [_element("app", "App"), _element("servidor", "Servidor", tags=["emProd"]),
                     _element("pessoa", "Pessoa", kind="pessoa")],
        "relations": [_arrow("app", "servidor", "pede"), _arrow("pessoa", "app")],
        "views": [{"id": "index", "type": "element", "title": "Tudo", "description": None, "tags": [], "links": [],
                   "viewOf": None, "steps": []}],
        "deployment": {"nodes": [], "instances": [], "relations": []},
    }


def test_the_same_model_draws_nothing():
    assert diff.markdown(_model(), _model()) == ""


def test_an_added_arrow_is_listed_as_new():
    pr = _model()
    pr["relations"].append(_arrow("servidor", "app", "responde"))
    text = diff.markdown(_model(), pr)
    assert text.startswith(diff.HEADING)
    assert "**Arrows**: 1 new\n- ➕ `servidor -> app: responde`\n" in text
    assert "Elements" not in text
    assert "Views" not in text


def test_a_removed_element_and_a_changed_one_show_what_differs():
    pr = _model()
    pr["elements"] = [_element("app", "App novo", tags=["emProd"]), _element("servidor", "Servidor", tags=["emProd"])]
    text = diff.markdown(_model(), pr)
    assert "**Elements**: 1 removed · 1 changed" in text
    assert "- ➖ `pessoa` (pessoa) `Pessoa`" in text
    assert "- ✏️ `app`: tags +`emProd`; title `App` → `App novo`" in text


def test_a_renamed_arrow_is_one_changed_arrow_not_a_removed_and_a_new_one():
    pr = _model()
    pr["relations"][0] = _arrow("app", "servidor", "consulta")
    text = diff.markdown(_model(), pr)
    assert "**Arrows**: 1 changed\n- ✏️ `app -> servidor: consulta` (was `app -> servidor: pede`)\n" in text
    assert "➕" not in text
    assert "➖" not in text


def test_a_renamed_arrow_that_also_changes_shows_the_other_fields():
    pr = _model()
    pr["relations"][0] = _arrow("app", "servidor", "consulta", technology="HTTP")
    text = diff.markdown(_model(), pr)
    assert "- ✏️ `app -> servidor: consulta` (was `app -> servidor: pede`): technology — → `HTTP`" in text


def test_renames_pair_only_arrows_with_the_same_ends_and_the_rest_stay_new_or_removed():
    base, pr = _model(), _model()
    base["relations"] = [_arrow("app", "servidor", "a"), _arrow("app", "servidor", "b"), _arrow("pessoa", "app")]
    pr["relations"] = [_arrow("app", "servidor", "c"), _arrow("app", "servidor", "d"), _arrow("app", "servidor", "e"),
                       _arrow("app", "pessoa")]
    text = diff.markdown(base, pr)
    assert "**Arrows**: 2 new · 1 removed · 2 changed" in text
    assert "`app -> servidor: c` (was `app -> servidor: a`)" in text
    assert "`app -> servidor: d` (was `app -> servidor: b`)" in text
    assert "- ➕ `app -> servidor: e`" in text
    assert "- ➕ `app -> pessoa`" in text
    assert "- ➖ `pessoa -> app`" in text


def test_a_changed_view_and_specification_are_listed():
    pr = _model()
    pr["views"][0]["steps"] = [{"source": "app", "target": "servidor", "title": "pede"}]
    pr["specification"]["tags"] = ["emProd", "novo"]
    text = diff.markdown(_model(), pr)
    assert '**Views**: 1 changed\n- ✏️ `index`: steps +`{"source": "app", "target": "servidor", "title": "pede"}`' in text
    assert "**Specification**: tags +`novo`" in text


def test_no_base_model_lists_everything_as_new_and_no_pr_model_everything_as_removed():
    new = diff.markdown(None, _model())
    assert "The base has no `arquitetura/modelo.json`: everything is new." in new
    assert "**Elements**: 3 new" in new
    assert "**Arrows**: 2 new" in new
    assert "**Views**: 1 new" in new
    gone = diff.markdown(_model(), None)
    assert "**Elements**: 3 removed" in gone
    assert "**Arrows**: 2 removed" in gone
    assert "everything is new" not in gone


def test_a_value_is_one_line_without_backticks_and_cut():
    assert diff._short("a `b`\n c") == "`a 'b' c`"
    long = diff._short("x" * 200)
    assert long == f"`{'x' * diff.SHORT}…`"


def test_a_change_past_the_cut_shows_around_the_first_difference():
    old, new = "a" * 150 + " fim", "a" * 150 + " outro fim"
    was, now = diff._pair(old, new)
    assert was != now
    assert was.startswith("`…")
    assert "fim" in was
    assert "outro fim" in now
    assert diff._pair("curto", "curta") == ("`curto`", "`curta`")
    assert diff._pair(None, "x") == ("—", "`x`")


def test_a_long_description_changed_at_its_end_shows_the_change():
    pr = _model()
    base = _model()
    base["elements"][0]["description"] = "Uma descrição longa do app. " * 5 + "Lê o lago."
    pr["elements"][0]["description"] = "Uma descrição longa do app. " * 5 + "Lê o servidor."
    line = next(x for x in diff.markdown(base, pr).splitlines() if x.startswith("- ✏️ `app`"))
    assert "Lê o lago." in line
    assert "Lê o servidor." in line


def test_a_list_shows_what_was_added_removed_or_reordered_and_a_dict_each_key():
    assert diff._field_change("steps", [{"a": 1}], [{"a": 1}, {"b": 2}]) == 'steps +`{"b": 2}`'
    assert diff._field_change("tags", ["x", "y"], ["y", "x"]) == "tags reordered"
    assert diff._field_change("tags", ["x", "x"], ["x"]) == "tags 2 → 1 items"
    assert diff._field_change("metadata", {"bead": "df-1", "doc": "a"}, {"bead": "df-2", "doc": "a", "novo": "s"}) == (
        "metadata.bead `df-1` → `df-2`; metadata.novo (absent) → `s`")


def test_a_renamed_step_of_a_dynamic_view_shows_the_step_and_the_title_that_changed():
    """Steps between real-length ids: their JSON passes SHORT characters before the title starts."""
    def step(title):
        return {"source": "casaAlvo.appCelularAlvo", "target": "casaAlvo.servidorAlvo", "title": title}

    first = {"source": "pessoaAlvo", "target": "casaAlvo.appCelularAlvo", "title": "abre o app"}
    base, pr = _model(), _model()
    base["views"][0] |= {"type": "dynamic", "steps": [first, step("envia a lista de compras")]}
    pr["views"][0] |= {"type": "dynamic", "steps": [first, step("envia a lista de desejos")]}
    assert len(diff._one_line(step(""))) > diff.SHORT
    assert ("- ✏️ `index`: steps[1].title `envia a lista de compras` → `envia a lista de desejos`"
            in diff.markdown(base, pr))


def test_changed_records_in_a_list_pair_only_when_as_many_went_as_came():
    assert diff._field_change("steps", [{"t": "a"}, {"t": "b"}], [{"t": "c"}]) == 'steps +`{"t": "c"}` −`{"t": "a"}` −`{"t": "b"}`'


def test_a_key_null_on_one_side_and_missing_on_the_other_is_a_change():
    assert diff._field_change("metadata", {"doc": None}, {}) == "metadata.doc — → (absent)"
    base, pr = _model(), _model()
    del pr["elements"][0]["technology"]
    assert "- ✏️ `app`: technology — → (absent)" in diff.markdown(base, pr)


@pytest.mark.parametrize("index", range(len(json.loads((ROOT / "arquitetura" / "modelo.json").read_text(encoding="utf-8"))["relations"])))
def test_each_arrow_of_the_real_model_added_by_a_pr_is_the_one_line_of_the_comment(index):
    pr = json.loads((ROOT / "arquitetura" / "modelo.json").read_text(encoding="utf-8"))
    base = copy.deepcopy(pr)
    arrow = base["relations"].pop(index)
    text = diff.markdown(base, pr)
    assert text == f"{diff.HEADING}\n\n**Arrows**: 1 new\n- ➕ `{arrow['id']}`" + (f" ({arrow['kind']})" if arrow["kind"] else "") + "\n"


def _cli(stdin: str) -> str:
    run = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, check=True,
                         cwd=ROOT, encoding="utf-8")
    return run.stdout


def test_the_command_line_reads_the_base_on_stdin_and_the_committed_model_as_the_pr():
    committed = (ROOT / "arquitetura" / "modelo.json").read_text(encoding="utf-8")
    assert _cli(committed) == ""
    assert _cli("").startswith(f"{diff.HEADING}\n\nThe base has no")


def _deployed() -> dict:
    model = _model()
    model["specification"] |= {"relationships": ["leitura"], "deployments": ["maquina"]}
    model["deployment"] = {
        "nodes": [_element("pc", "PC da casa", kind="maquina")],
        "instances": [{"id": "pc.app", "element": "app", "title": "App", "description": None, "technology": None,
                       "tags": [], "metadata": {}, "links": []}],
        "relations": [_arrow("pc.app", "pc.servidor", "pede")],
    }
    return model


def test_deployment_nodes_instances_and_arrows_are_listed():
    base, pr = _deployed(), _deployed()
    pr["deployment"]["nodes"].append(_element("celular", "Celular", kind="maquina"))
    pr["deployment"]["instances"][0]["technology"] = "Python"
    pr["deployment"]["relations"].append(_arrow("pc.app", "celular", "", kind="leitura"))
    text = diff.markdown(base, pr)
    assert "**Deployment nodes**: 1 new\n- ➕ `celular` (maquina) `Celular`\n" in text
    assert "**Deployment instances**: 1 changed\n- ✏️ `pc.app`: technology — → `Python`\n" in text
    assert "**Deployment arrows**: 1 new\n- ➕ `pc.app -> celular` (leitura)\n" in text


def test_a_renamed_deployment_arrow_is_one_changed_arrow():
    base, pr = _deployed(), _deployed()
    pr["deployment"]["relations"] = [_arrow("pc.app", "pc.servidor", "consulta")]
    text = diff.markdown(base, pr)
    assert "**Deployment arrows**: 1 changed\n- ✏️ `pc.app -> pc.servidor: consulta` (was `pc.app -> pc.servidor: pede`)\n" in text


def test_the_relationship_and_deployment_kinds_of_the_specification_are_listed():
    base, pr = _deployed(), _deployed()
    pr["specification"] |= {"relationships": [], "deployments": ["maquina", "ambiente"]}
    assert "**Specification**: relationships −`leitura`; deployments +`ambiente`" in diff.markdown(base, pr)


def test_a_new_likec4_version_is_a_line_and_alone_still_makes_a_comment():
    pr = _model()
    pr["likec4"] = "1.62.0"
    assert diff.markdown(_model(), pr) == f"{diff.HEADING}\n\n**LikeC4**: `1.61.1` → `1.62.0`\n"


def test_an_element_whose_title_is_in_its_id_shows_its_title():
    pr = _model()
    pr["elements"].append(_element("lago", "lago"))
    assert "- ➕ `lago` (conteiner) `lago`" in diff.markdown(_model(), pr)


MALFORMED = {
    "not-an-object": [],
    "elements-not-a-list": {"elements": {"app": {}}},
    "a-record-without-an-id": {"elements": [{"title": "App"}]},
    "an-arrow-without-a-target": {"relations": [{"id": "a -> b", "source": "a"}]},
    "deployment-not-an-object": {"deployment": []},
    "an-id-twice": {"views": [{"id": "index"}, {"id": "index"}]},
}


@pytest.mark.parametrize("bad", MALFORMED.values(), ids=MALFORMED.keys())
@pytest.mark.parametrize("side", ["base", "PR"])
def test_a_malformed_model_on_either_side_is_refused_naming_the_side(bad, side):
    with pytest.raises(ValueError, match=f"the {side} model"):
        diff.markdown(bad, _model()) if side == "base" else diff.markdown(_model(), bad)


def _cli_failure(stdin: str) -> str:
    run = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, check=False,
                         cwd=ROOT, encoding="utf-8")
    assert run.returncode == 1
    assert run.stdout == ""
    assert "Traceback" not in run.stderr
    return run.stderr


def test_the_command_line_refuses_a_malformed_base_with_a_message():
    assert "the base model is not JSON" in _cli_failure("{not json")
    assert "the base model's elements is not a list of records with id" in _cli_failure('{"elements": [{"title": "x"}]}')


def test_the_command_line_refuses_a_pr_model_that_is_not_json(tmp_path, monkeypatch):
    broken = tmp_path / "modelo.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(diff.modelo, "SAIDA", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="the PR model .* is not JSON"):
        diff.main()
