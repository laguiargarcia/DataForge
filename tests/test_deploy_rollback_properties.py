"""Rules of dataforge/deploy/rollback.py: undoing deploys, last first (law #4, D18, and the
crash-safety contract in docs/architecture.md).

Each example builds its workspaces in a temporary directory created inside the example, because
a test-scoped fixture would be shared between examples.
"""
import tempfile
import warnings
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st

from dataforge.deploy.apply import apply_plan
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.models import Action, DeployEntry, ItemChange, Kind, PairRule, PipelineModel
from dataforge.deploy.pairing import resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.rollback import _prune_empty_dirs, rollback_last

# Folders never contain a dot and files always end in .py, so a file never collides with a folder.
# Few names and contents, so deploys often touch the same paths.
_DIR = st.sampled_from(["a", "b", "sub"])
_FILE = st.sampled_from(["x.py", "y.py", "z.py"])
_REL = st.builds(lambda dirs, name: "/".join(["scripts", *dirs, name]), st.lists(_DIR, max_size=2), _FILE)
_TREE = st.dictionaries(_REL, st.sampled_from([b"", b"1", b"2\n"]), max_size=5)


def _plant(root: Path, tree: dict[str, bytes]) -> None:
    for rel, data in tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _files(root: Path) -> dict[str, bytes]:
    """Every file under root except the deploy ledger, by relative path."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not p.relative_to(root).as_posix().startswith("deploy/")}


def _dirs(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_dir() and not p.relative_to(root).as_posix().startswith("deploy")}


def _deploy(source: Path, target: Path, prune: bool, ledger: Ledger):
    pipeline = PipelineModel(target="Prod", prune=prune,
                             pairs=[PairRule(source="scripts/**", target="scripts/")])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        plan = build_plan(source, target, pipeline, "p", DeployIgnore.load(source),
                          resolve_pairs(source, pipeline))
    return apply_plan(source, target, plan, ledger)


@settings(max_examples=40)
@given(_TREE, st.lists(st.tuples(_TREE.filter(bool), st.booleans()), min_size=1, max_size=3))
@example({"scripts/a/x.py": b"1"}, [({"scripts/a/y.py": b"2\n"}, True)])   # DELETE + ADD, one dir
@example({}, [({"scripts/a/sub/x.py": b"1"}, False),
              ({"scripts/b/y.py": b""}, True)])   # the second prunes the first's file
@example({"scripts/x.py": b"1"}, [({"scripts/x.py": b"2\n"}, False),
                                  ({"scripts/x.py": b""}, False)])   # MODIFY twice
def test_rolling_back_walks_the_target_back_through_every_deploy(initial, deploys):
    """After any sequence of deploys (ADD, MODIFY, prune DELETE), each rollback leaves the files
    exactly as they were before that deploy and no folder that did not exist then; once every
    deploy is undone the log is empty and rollback has nothing left to do."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        target = root / "Prod"
        target.mkdir()
        _plant(target, initial)
        ledger = Ledger(target)
        before = []
        for n, (tree, prune) in enumerate(deploys):
            source = root / f"Dev{n}"
            source.mkdir()
            _plant(source, tree)
            state = (_files(target), _dirs(target))
            recorded = len(ledger.entries())
            _deploy(source, target, prune, ledger)
            if len(ledger.entries()) > recorded:   # a deploy with nothing to write records nothing
                before.append(state)
        for files_then, dirs_then in reversed(before):
            assert rollback_last(target, ledger) is not None
            assert _files(target) == files_then
            assert _dirs(target) <= dirs_then
        assert ledger.entries() == []
        assert rollback_last(target, ledger) is None
        assert target.is_dir()


def _entry(*items: ItemChange, eid: str = "dpl_1") -> DeployEntry:
    return DeployEntry(id=eid, ts="t", source="Dev", target="Prod", pipeline="p", items=list(items))


def _item(rel: str, action: Action, before: str | None = None) -> ItemChange:
    return ItemChange(source_path=rel, target_path=rel, kind=Kind.script, action=action,
                      before_sha=before)


def test_rolling_back_adds_in_several_folders_removes_every_folder_they_emptied(tmp_path):
    tgt = tmp_path / "Prod"
    _plant(tgt, {"scripts/a/x.py": b"1", "scripts/b/c/y.py": b"2", "jobs/j/job.yaml": b"3",
                 "config/keep.yaml": b"k"})
    led = Ledger(tgt)
    led.append(_entry(_item("scripts/a/x.py", Action.add), _item("scripts/b/c/y.py", Action.add),
                      _item("jobs/j/job.yaml", Action.add)))
    rollback_last(tgt, led)
    assert _files(tgt) == {"config/keep.yaml": b"k"}
    assert _dirs(tgt) == {"config"}


@pytest.mark.parametrize("damage", ["missing", "torn"])
def test_a_missing_or_torn_snapshot_aborts_before_any_file_is_touched(tmp_path, damage):
    """D18: rollback checks every snapshot first, so a bad one never leaves a half-undone target,
    and a torn one is never restored. The error names the file whose snapshot is bad."""
    tgt = tmp_path / "Prod"
    _plant(tgt, {"scripts/added.py": b"new", "scripts/changed.py": b"new"})
    led = Ledger(tgt)
    snap = led.write_blob(b"old content\n")
    blob = next(p for p in led.objects.rglob("*") if p.is_file())
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"old")
    led.append(_entry(_item("scripts/added.py", Action.add),
                      _item("scripts/changed.py", Action.modify, snap)))
    with pytest.raises(FileNotFoundError, match="scripts/changed.py"):
        rollback_last(tgt, led)
    assert _files(tgt) == {"scripts/added.py": b"new", "scripts/changed.py": b"new"}
    assert led.last().id == "dpl_1"


def test_rollback_after_the_deployed_files_were_removed_still_restores_the_old_content(tmp_path):
    """A target someone deleted after the deploy: a MODIFY/DELETE gets its old content back, an
    ADD that is already gone is no error."""
    tgt = tmp_path / "Prod"
    tgt.mkdir()
    led = Ledger(tgt)
    led.append(_entry(_item("scripts/sub/changed.py", Action.modify, led.write_blob(b"old\n")),
                      _item("scripts/deleted.py", Action.delete, led.write_blob(b"gone\n")),
                      _item("scripts/new/added.py", Action.add)))
    assert rollback_last(tgt, led).id == "dpl_1"
    assert _files(tgt) == {"scripts/sub/changed.py": b"old\n", "scripts/deleted.py": b"gone\n"}
    assert led.entries() == []


def test_pruning_never_removes_the_target_or_a_folder_outside_it(tmp_path):
    """_prune_empty_dirs stops at the target and never walks outside it, whatever folder it is
    asked to start from."""
    tgt = tmp_path / "Prod"
    (tgt / "scripts" / "empty").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "empty"
    outside.mkdir(parents=True)
    _prune_empty_dirs(tgt, outside)
    assert outside.is_dir()
    _prune_empty_dirs(tgt, tgt)
    assert tgt.is_dir()
    _prune_empty_dirs(tgt, tgt / "scripts" / "empty")
    assert tgt.is_dir()
    assert not (tgt / "scripts").exists()


# --- a target that changed between plan and apply (TOCTOU) -----------------------------------------

def _plan_then(tmp_path, source_tree, target_tree, between):
    """Plan Dev -> Prod, run `between` on the target, then apply. Returns (target, ledger)."""
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _plant(src, source_tree)
    _plant(tgt, target_tree)
    tgt.mkdir(exist_ok=True)
    pipeline = PipelineModel(target="Prod", pairs=[PairRule(source="scripts/**", target="scripts/")])
    plan = build_plan(src, tgt, pipeline, "p", DeployIgnore.load(src), resolve_pairs(src, pipeline))
    between(tgt)
    led = Ledger(tgt)
    apply_plan(src, tgt, plan, led)
    return tgt, led


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.9: rollback of a MODIFY with no snapshot leaves the deployed file")
def test_rollback_removes_a_modify_whose_target_was_gone_when_the_apply_wrote_it(tmp_path):
    """The apply found no file to snapshot (removed after the plan), so before the deploy the target
    had none: rollback must leave none."""
    tgt, led = _plan_then(tmp_path, {"scripts/a.py": b"new\n"}, {"scripts/a.py": b"old\n"},
                          lambda t: (t / "scripts/a.py").unlink())
    assert led.last().items[0].before_sha is None
    rollback_last(tgt, led)
    assert _files(tgt) == {}


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.5.11: rollback of an ADD deletes a file whose snapshot it holds")
def test_rollback_restores_an_add_whose_target_appeared_before_the_apply_wrote_it(tmp_path):
    """The apply snapshotted the file it overwrote (it appeared after the plan): rollback must put
    it back, not delete it."""
    tgt, led = _plan_then(tmp_path, {"scripts/a.py": b"new\n"}, {},
                          lambda t: _plant(t, {"scripts/a.py": b"someone else's\n"}))
    assert led.last().items[0].before_sha is not None
    rollback_last(tgt, led)
    assert _files(tgt) == {"scripts/a.py": b"someone else's\n"}

