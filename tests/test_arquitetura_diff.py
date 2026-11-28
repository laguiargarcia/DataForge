"""scripts/arquitetura/diff.py: what a PR changes in arquitetura/modelo.json, as the Markdown of its
PR comment (df-8x0m.23.38). The models below have modelo.json's layout (scripts/arquitetura/modelo.py)."""

import copy
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
    assert '**Views**: 1 changed\n- ✏️ `index`: steps `[]` → `[{"source": "app", "target": "servidor", "title": "pede"}]`' in text
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
    assert len(long) == diff.SHORT + 2
    assert long.endswith("…`")


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
