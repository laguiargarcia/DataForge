"""Rules of dataforge/deploy/service.py, the deploy facade the CLI, the API and the GUI call: the four
laws hold whatever `only` selects, results count what the plan holds, and every failure reaches the
front-ends as a DeployServiceError naming what went wrong."""
import sys
import tempfile
import warnings
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st

from dataforge.deploy import service
from dataforge.deploy.models import Action, AutoDeployStatus, DeployEntry, ItemChange, Kind
from dataforge.deploy.pairing import load_pipeline
from dataforge.deploy.service import DeployServiceError
from dataforge.parser import load_triggers


def _plant(root: Path, tree: dict[str, str]) -> None:
    for rel, text in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _trigger(name: str, enabled: bool = True) -> str:
    return f"name: {name}\ncron: '0 5 * * *'\nenabled: {str(enabled).lower()}\njobs: []\n"


_PIPELINE = ("target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"
             "  - {source: 'triggers/**', target: 'triggers/'}\n")
# One of each action: ADD a.py, MODIFY b.py, prune DELETE old.py, SKIP_IGNORED secret.py, and the
# PROTECTED prune of an active trigger absent from the source (law #2'); daily.yaml is in sync.
_SOURCE = {"scripts/a.py": "a\n", "scripts/b.py": "b new\n", "scripts/secret.py": "TOKEN=1\n",
           "triggers/daily.yaml": _trigger("daily"), "deploy/.deployignore": "scripts/secret.py\n",
           "deploy/to-prod.yaml": _PIPELINE}
_TARGET = {"scripts/b.py": "b old\n", "scripts/old.py": "old\n", "scripts/secret.py": "prod secret\n",
           "triggers/daily.yaml": _trigger("daily"), "triggers/extra.yaml": _trigger("extra")}
_PATHS = ["scripts/a.py", "scripts/b.py", "scripts/old.py", "scripts/secret.py",
          "triggers/extra.yaml", "triggers/daily.yaml", "scripts/not-in-plan.py"]


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not p.relative_to(root).as_posix().startswith("deploy/")}


@settings(max_examples=30)
@given(st.none() | st.sets(st.sampled_from(_PATHS)))
@example(None)
@example(set())
@example({"scripts/secret.py", "triggers/extra.yaml"})   # only the items the laws keep: nothing
@example(set(_PATHS))
def test_apply_writes_exactly_the_selected_writable_items(only):
    """apply writes the plan's ADD/MODIFY/DELETE items whose target is in `only` (all of them for
    None), in plan order, and nothing else: an ignored or protected path in `only` stays untouched
    (laws #2'/#3), a path not in the plan is ignored, and an empty selection records no entry."""
    with tempfile.TemporaryDirectory() as d:
        src, tgt = Path(d, "Dev"), Path(d, "Prod")
        _plant(src, _SOURCE)
        _plant(tgt, _TARGET)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            plan = service.build(src, load_pipeline(src, "to-prod"), "to-prod", tgt)
        assert {i.target_path: i.action for i in plan.items} == {
            "scripts/a.py": Action.add, "scripts/b.py": Action.modify, "scripts/old.py": Action.delete,
            "scripts/secret.py": Action.skip_ignored, "triggers/extra.yaml": Action.protected}
        before = _files(tgt)
        chosen = [i.target_path for i in plan.writable() if only is None or i.target_path in only]

        entry = service.apply(src, tgt, plan, only)

        after = _files(tgt)
        if not chosen:
            assert entry is None
            assert service.last_entry(tgt) is None
        else:
            assert [i.target_path for i in entry.items] == chosen
            assert (entry.source, entry.target, entry.pipeline) == (plan.source, plan.target, "to-prod")
            assert service.last_entry(tgt) == entry
        changed = {rel for rel in before.keys() | after.keys() if before.get(rel) != after.get(rel)}
        assert changed == set(chosen)


def test_auto_deploy_counts_what_the_plan_holds_and_names_the_triggers_it_touched(dataforge_home):
    """D21: the result counts every item of the plan by what happened to it, and lists the triggers
    the front-ends must reconcile (D23)."""
    dev, prod = dataforge_home / "workspaces" / "Dev", dataforge_home / "workspaces" / "Prod"
    _plant(dev, {"workspace.yaml": "name: Dev\n", **_SOURCE,
                 "triggers/daily.yaml": _trigger("daily").replace("0 5", "0 9")})
    _plant(prod, {"workspace.yaml": "name: Prod\n", **_TARGET})
    r = service.auto_deploy("Dev", "to-prod")
    assert r.status == AutoDeployStatus.applied
    assert (r.source, r.target, r.pipeline) == ("Dev", "Prod", "to-prod")
    assert (r.applied, r.protected, r.ignored, r.total) == (4, 1, 1, 6)
    assert r.entry_id == service.last_entry(prod).id
    assert r.triggers == ["daily"]


def test_auto_deploy_with_nothing_to_write_still_counts_the_plan(dataforge_home):
    """A run the laws leave with nothing to write is 'nothing to apply', never 'up to date' hiding
    the protected and ignored items (D21)."""
    dev, prod = dataforge_home / "workspaces" / "Dev", dataforge_home / "workspaces" / "Prod"
    source = {k: v for k, v in _SOURCE.items() if k not in ("scripts/a.py", "scripts/b.py")}
    target = {k: v for k, v in _TARGET.items() if k not in ("scripts/b.py", "scripts/old.py")}
    _plant(dev, {"workspace.yaml": "name: Dev\n", **source, "scripts/c.py": "c\n"})
    _plant(prod, {"workspace.yaml": "name: Prod\n", **target, "scripts/c.py": "c\n"})
    r = service.auto_deploy("Dev", "to-prod")
    assert r.status == AutoDeployStatus.nothing_to_apply
    assert (r.applied, r.protected, r.ignored, r.total) == (0, 1, 1, 2)
    assert (r.entry_id, r.triggers) == (None, [])
    assert service.last_entry(prod) is None


def test_auto_deploy_writes_only_the_paths_it_is_given(dataforge_home):
    dev, prod = dataforge_home / "workspaces" / "Dev", dataforge_home / "workspaces" / "Prod"
    _plant(dev, {"workspace.yaml": "name: Dev\n", **_SOURCE})
    _plant(prod, {"workspace.yaml": "name: Prod\n", **_TARGET})
    r = service.auto_deploy("Dev", "to-prod", only={"scripts/a.py"})
    assert (r.status, r.applied) == (AutoDeployStatus.applied, 1)
    assert [i.target_path for i in service.last_entry(prod).items] == ["scripts/a.py"]
    assert (prod / "scripts/old.py").exists()


# --- errors reach the front-ends as DeployServiceError ---------------------------------------------

def _ws(home: Path, name: str, tree: dict[str, str] | None = None) -> Path:
    ws = home / "workspaces" / name
    _plant(ws, {"workspace.yaml": f"name: {name}\n", **(tree or {})})
    return ws


@pytest.mark.parametrize(("setup", "pipeline", "kind", "named"), [
    ("no workspace", "to-prod", "not_found", "NoSuch"),
    ("no pipeline", "nope", "not_found", "nope"),
    ("malformed yaml", "bad", "invalid", "bad"),
    ("not a mapping", "list", "invalid", "list"),
    ("wrong field type", "typed", "invalid", "typed"),
    ("no target workspace", "to-gone", "not_found", "Gone"),
    ("invalid target name", "to-bad", "not_found", "../x"),
])
def test_resolve_failures_are_service_errors_that_name_the_culprit(dataforge_home, setup, pipeline,
                                                                    kind, named):
    """The API turns kind into 404/400 and shows the message; the message names the workspace or
    pipeline at fault, so the user knows what to fix."""
    _ws(dataforge_home, "Dev", {
        "deploy/bad.yaml": "target: [unclosed\n", "deploy/list.yaml": "- target: Prod\n",
        "deploy/typed.yaml": "target: Prod\npairs: 3\n", "deploy/to-gone.yaml": "target: Gone\n",
        "deploy/to-bad.yaml": "target: ../x\n"})
    with pytest.raises(DeployServiceError) as ei:
        service.resolve("NoSuch" if setup == "no workspace" else "Dev", pipeline)
    assert ei.value.kind == kind
    assert named in str(ei.value)


@pytest.mark.xfail(strict=True, raises=UnicodeDecodeError,
                   reason="df-ahua.23.10: a pipeline file that is not UTF-8 escapes as a raw error")
def test_a_pipeline_file_that_is_not_utf8_is_an_invalid_pipeline(dataforge_home):
    """resolve's promise: any failure is a DeployServiceError. A pipeline saved as ANSI (cp1252) by a
    Windows editor is invalid, like malformed YAML, not a crash of the front-end."""
    _ws(dataforge_home, "Dev")
    ansi = dataforge_home / "workspaces/Dev/deploy/ansi.yaml"
    ansi.parent.mkdir(parents=True)
    ansi.write_bytes("target: Prod\ndescription: relatório\npairs: []\n".encode("cp1252"))
    with pytest.raises(DeployServiceError) as ei:
        service.resolve("Dev", "ansi")
    assert ei.value.kind == "invalid"


def test_build_failure_says_why_the_pipeline_is_invalid(dataforge_home):
    dev = _ws(dataforge_home, "Dev", {"scripts/a.py": "a\n", "deploy/esc.yaml":
                                      "target: Prod\npairs:\n  - {source: 'scripts/a.py', target: '../../etc/'}\n"})
    _ws(dataforge_home, "Prod")
    src, pl, tgt = service.resolve("Dev", "esc")
    with pytest.raises(DeployServiceError) as ei:
        service.build(src, pl, "esc", tgt)
    assert ei.value.kind == "invalid"
    assert "../../etc/" in str(ei.value)
    assert dev.is_dir()


def test_list_pipelines_of_a_workspace_without_a_deploy_folder_is_empty(tmp_path):
    assert service.list_pipelines(tmp_path) == []
    (tmp_path / "deploy").write_bytes(b"a file, not a folder")
    assert service.list_pipelines(tmp_path) == []


# --- trigger_names: the triggers the front-ends reconcile (D23) -------------------------------------

def _entry(*items: tuple[str, Kind]) -> DeployEntry:
    return DeployEntry(id="dpl_1", ts="t", source="Dev", target="Prod", pipeline="p", items=[
        ItemChange(source_path=rel, target_path=rel, kind=kind, action=Action.modify)
        for rel, kind in items])


def test_trigger_names_lists_the_triggers_under_triggers_and_nothing_else():
    entry = _entry(("triggers/daily.yaml", Kind.trigger), ("config/renamed.yaml", Kind.trigger),
                   ("jobs/etl.yaml", Kind.job), ("triggers/notes.txt", Kind.trigger),
                   ("triggers/a.b.yaml", Kind.trigger))
    assert service.trigger_names(entry) == ["daily", "a.b"]
    assert service.trigger_names(None) == []


_NTFS_CASE = pytest.mark.xfail(sys.platform == "win32", strict=True, raises=AssertionError,
                               reason="df-ahua.23.11: NTFS ignores case, trigger_names does not")


@pytest.mark.parametrize("rel", [
    "triggers/daily.yaml",
    pytest.param("Triggers/daily.yaml", marks=_NTFS_CASE),
    pytest.param("TRIGGERS/daily.yaml", marks=_NTFS_CASE),
    pytest.param("triggers/daily.YAML", marks=_NTFS_CASE),
])
def test_trigger_names_agrees_with_the_triggers_the_engine_reads(tmp_path, rel):
    """Every trigger a deploy wrote that the engine then loads gets reconciled, and only those: the
    names agree with parser.load_triggers on the target, whatever the file system does with case."""
    _plant(tmp_path, {rel: _trigger("daily")})
    assert service.trigger_names(_entry((rel, Kind.trigger))) == [
        t.name for t in load_triggers(tmp_path)]
