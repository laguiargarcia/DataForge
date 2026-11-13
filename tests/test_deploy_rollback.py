from pathlib import Path
import pytest
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.apply import apply_plan
from dataforge.deploy.rollback import rollback_last


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _plan(src: Path, tgt: Path, pipeline_yaml: str, name: str = "p"):
    _mk(src, f"deploy/{name}.yaml", pipeline_yaml)
    p = load_pipeline(src, name)
    return build_plan(src, tgt, p, name, DeployIgnore.load(src), resolve_pairs(src, p))


_SCRIPTS = "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"


def test_rollback_of_add_removes_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert (tgt / "scripts/a.py").exists()
    rollback_last(tgt, led)
    assert not (tgt / "scripts/a.py").exists()
    assert led.last() is None


def test_rollback_of_modify_restores_old_content(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/a.py", "old\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert (tgt / "scripts/a.py").read_text() == "new\n"
    rollback_last(tgt, led)
    assert (tgt / "scripts/a.py").read_text() == "old\n"


def test_rollback_of_delete_restores_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/old.py", "old\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt,
               "target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"), led)
    assert not (tgt / "scripts/old.py").exists()
    rollback_last(tgt, led)
    assert (tgt / "scripts/old.py").read_text() == "old\n"


def test_rollback_of_add_prunes_emptied_dirs(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/sub/a.py", "new\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert (tgt / "scripts/sub/a.py").exists()
    rollback_last(tgt, led)
    assert not (tgt / "scripts/sub/a.py").exists()
    assert not (tgt / "scripts/sub").exists()   # dir the ADD created, now emptied -> pruned
    assert not (tgt / "scripts").exists()         # and its now-empty parent
    assert tgt.exists()                           # but never the target root itself


def test_rollback_of_add_keeps_dir_with_remaining_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/keep.txt", "keep\n")        # pre-existing, not part of the deploy
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    rollback_last(tgt, led)
    assert not (tgt / "scripts/a.py").exists()
    assert (tgt / "scripts/keep.txt").read_text() == "keep\n"   # untouched
    assert (tgt / "scripts").is_dir()             # dir kept — it still holds a file


def test_rollback_empty_log_returns_none(tmp_path):
    assert rollback_last(tmp_path / "Prod", Ledger(tmp_path / "Prod")) is None


def test_rollback_aborts_if_snapshot_blob_missing(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/a.py", "old\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    item = led.last().items[0]
    hexd = item.before_sha.split(":")[-1]
    (tgt / "deploy/.ledger/objects" / hexd[:2] / hexd).unlink()   # corrupt the snapshot
    with pytest.raises(FileNotFoundError):
        rollback_last(tgt, led)
    assert led.last() is not None                          # entry retained for retry
    assert (tgt / "scripts/a.py").read_text() == "new\n"   # target untouched (pre-flight aborted)


# --- df-m1dk: rollback never writes or deletes outside the target, whatever the ledger says ---

@pytest.mark.parametrize("action", ["ADD", "MODIFY", "DELETE"])
@pytest.mark.parametrize("hostile", ["../escaped.txt", "scripts/../../escaped.txt"])
def test_rollback_refuses_a_ledger_path_outside_the_target(tmp_path, hostile, action):
    from dataforge.deploy.models import Action, DeployEntry, ItemChange, Kind
    tgt = tmp_path / "Prod"
    _mk(tgt, "scripts/ok.py", "deployed\n")
    (tmp_path / "escaped.txt").write_text("precious")
    led = Ledger(tgt)
    snap = led.write_blob(b"evil")
    led.append(DeployEntry(id="dpl_x", ts="2026-11-11T00:00:00+00:00", parent=None, source="Dev",
                           target="Prod", pipeline="p", items=[
        ItemChange(source_path=None, target_path=hostile, kind=Kind.script,
                   action=Action(action), before_sha=None if action == "ADD" else snap)]))
    with pytest.raises(ValueError, match="outside the target workspace|escapes target workspace"):
        rollback_last(tgt, led)
    assert (tmp_path / "escaped.txt").read_text() == "precious"
    assert led.last() is not None   # entry kept: nothing was rolled back


# --- df-ahua.5.1: an unfinished entry (an apply that died) is undone as it stands ---

def test_rollback_of_an_unfinished_entry_the_apply_never_reached_changes_nothing(tmp_path):
    from dataforge.deploy.models import Action, DeployEntry, EntryStatus, ItemChange, Kind
    tgt = tmp_path / "Prod"
    _mk(tgt, "scripts/a.py", "old\n")
    _mk(tgt, "scripts/gone.py", "gone\n")
    led = Ledger(tgt)
    led.append(DeployEntry(id="dpl_1", ts="t", source="Dev", target="Prod", pipeline="p"))

    def item(rel, action, before):
        return ItemChange(source_path=rel, target_path=rel, kind=Kind.script, action=action,
                          before_sha=before)

    led.append(DeployEntry(id="dpl_2", ts="t", source="Dev", target="Prod", pipeline="p",
                           status=EntryStatus.in_progress, items=[
        item("scripts/a.py", Action.modify, led.write_blob(b"old\n")),
        item("scripts/new/b.py", Action.add, None),
        item("scripts/gone.py", Action.delete, led.write_blob(b"gone\n"))]))
    assert rollback_last(tgt, led).id == "dpl_2"
    assert sorted(p.relative_to(tgt).as_posix() for p in (tgt / "scripts").rglob("*")) == [
        "scripts/a.py", "scripts/gone.py"]
    assert (tgt / "scripts/a.py").read_text() == "old\n"
    assert [e.id for e in led.entries()] == ["dpl_1"]


def test_rollback_reads_an_entry_written_before_entries_had_a_status(tmp_path):
    tgt = tmp_path / "Prod"
    _mk(tgt, "scripts/a.py", "new\n")
    led = Ledger(tgt)
    snap = led.write_blob(b"old\n")
    led.log.write_bytes(('{"id":"dpl_old","ts":"t","source":"Dev","target":"Prod","pipeline":"p",'
                         '"parent":null,"items":[{"source_path":"scripts/a.py","target_path":'
                         '"scripts/a.py","kind":"script","action":"MODIFY","reason":"",'
                         f'"before_sha":"{snap}","after_sha":null}}]}}\n').encode())
    assert rollback_last(tgt, led).id == "dpl_old"
    assert (tgt / "scripts/a.py").read_text() == "old\n"
    assert led.entries() == []
