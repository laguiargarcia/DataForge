import os
import sys
from pathlib import Path
import pytest
import yaml
from dataforge.deploy.models import Action, Kind
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.apply import apply_plan

_SCRIPTS = "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _plan(src: Path, tgt: Path, pipeline_yaml: str, name: str = "p"):
    _mk(src, f"deploy/{name}.yaml", pipeline_yaml)
    p = load_pipeline(src, name)
    return build_plan(src, tgt, p, name, DeployIgnore.load(src), resolve_pairs(src, p))


def test_apply_add_writes_file_and_records_entry(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    led = Ledger(tgt)
    entry = apply_plan(src, tgt, plan, led)
    assert (tgt / "scripts/a.py").read_text() == "print(1)\n"
    assert led.last().id == entry.id
    assert [i.action for i in entry.items] == [Action.add]


def test_apply_modify_overwrites_and_snapshots_old(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/a.py", "old\n")
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    led = Ledger(tgt)
    entry = apply_plan(src, tgt, plan, led)
    assert (tgt / "scripts/a.py").read_text() == "new\n"
    item = entry.items[0]
    assert item.action == Action.modify
    assert led.read_blob(item.before_sha) == b"old\n"


def test_apply_delete_removes_and_snapshots(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/old.py", "old\n")
    plan = _plan(src, tgt,
                 "target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    led = Ledger(tgt)
    entry = apply_plan(src, tgt, plan, led)
    assert not (tgt / "scripts/old.py").exists()
    deleted = [i for i in entry.items if i.target_path == "scripts/old.py"][0]
    assert deleted.action == Action.delete
    assert led.read_blob(deleted.before_sha) == b"old\n"


def test_apply_trigger_add_is_not_armed(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    apply_plan(src, tgt, plan, Ledger(tgt))
    written = yaml.safe_load((tgt / "triggers/daily.yaml").read_text())
    assert written["enabled"] is False
    assert written["cron"] == "0 9 * * *"


def test_apply_trigger_modify_preserves_target_enabled(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: false\njobs: []\n")
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    apply_plan(src, tgt, plan, Ledger(tgt))
    written = yaml.safe_load((tgt / "triggers/daily.yaml").read_text())
    assert written["enabled"] is False
    assert written["cron"] == "0 9 * * *"


def test_apply_trigger_modify_preserves_active_target(tmp_path):
    # Law #2': an ACTIVE target trigger's definition is updated, but its on/off status is preserved.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    entry = apply_plan(src, tgt, plan, Ledger(tgt))
    written = yaml.safe_load((tgt / "triggers/daily.yaml").read_text())
    assert written["enabled"] is True        # status preserved — still active
    assert written["cron"] == "0 9 * * *"     # definition updated
    assert [i.action for i in entry.items] == [Action.modify]


def test_apply_records_partial_entry_on_midloop_failure(tmp_path, monkeypatch):
    import os as _os
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "A\n")
    _mk(src, "scripts/b.py", "B\n")
    plan = _plan(src, tgt, _SCRIPTS)
    led = Ledger(tgt)
    real_replace = _os.replace
    def boom(s, d):
        if str(d).endswith("b.py"):
            raise OSError("disk full")
        return real_replace(s, d)
    monkeypatch.setattr(_os, "replace", boom)
    with pytest.raises(OSError):
        apply_plan(src, tgt, plan, led)
    assert led.last() is not None                       # partial deploy IS recorded
    paths = {i.target_path for i in led.last().items}
    assert "scripts/a.py" in paths                      # the write that happened is recoverable
    assert "scripts/b.py" not in paths


def test_apply_never_targets_its_own_ledger(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)   # creates the ledger
    _mk(src, "deploy/keep.yaml", "x\n")
    plan2 = _plan(src, tgt,
                  "target: Prod\nprune: true\npairs:\n  - {source: 'deploy/**', target: 'deploy/'}\n", "evil")
    assert all(not i.target_path.startswith("deploy/.ledger") for i in plan2.items)
    apply_plan(src, tgt, plan2, Ledger(tgt))
    assert (tgt / "deploy/.ledger/deploylog.jsonl").exists()   # history survived


def test_apply_dirty_source_trigger_does_not_crash(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/bad.yaml", "- a\n- b\n")            # a list, not a mapping
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    apply_plan(src, tgt, plan, Ledger(tgt))               # must not raise
    assert (tgt / "triggers/bad.yaml").exists()


def test_deploy_entries_chain_via_parent(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    led = Ledger(tgt)
    e1 = apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    _mk(src, "scripts/b.py", "b\n")
    e2 = apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS, "p2"), led)
    assert e1.parent is None
    assert e2.parent == e1.id


_TRIGGERS = "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n"
_SOURCE_TRIGGER = "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n"


def test_apply_skips_a_ledger_item_and_still_applies_the_rest(tmp_path):
    # Law #4 holds in apply itself, not only in the planner: a plan that names the ledger
    # (hand-built, or from a future planner bug) must not touch it nor stop the deploy.
    from dataforge.deploy.models import DeployPlan, ItemChange
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)          # creates the ledger
    log_before = (tgt / "deploy/.ledger/deploylog.jsonl").read_bytes()
    _mk(src, "scripts/b.py", "b\n")
    plan = DeployPlan(source="Dev", target="Prod", pipeline="p", items=[
        ItemChange(source_path=None, target_path="deploy/.ledger/deploylog.jsonl",
                   kind=Kind.other, action=Action.delete),
        ItemChange(source_path="scripts/b.py", target_path="scripts/b.py",
                   kind=Kind.script, action=Action.add),
    ])
    entry = apply_plan(src, tgt, plan, led)
    assert (tgt / "scripts/b.py").read_text() == "b\n"
    assert [i.target_path for i in entry.items] == ["scripts/b.py"]
    assert (tgt / "deploy/.ledger/deploylog.jsonl").read_bytes().startswith(log_before)


def test_apply_with_nothing_to_write_records_no_entry(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/a.py", "a\n")                                # already in sync
    led = Ledger(tgt)
    entry = apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert entry.items == []
    assert led.last() is None                                      # no empty entry to roll back


def test_apply_unreadable_target_trigger_degrades_to_disarmed(tmp_path):
    # Law #1 and D14: a target trigger that is not a YAML mapping gives no 'enabled' to keep,
    # so the promoted trigger is written disarmed, and apply does not crash.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", _SOURCE_TRIGGER)
    _mk(tgt, "triggers/daily.yaml", "- not\n- a mapping\n")
    apply_plan(src, tgt, _plan(src, tgt, _TRIGGERS), Ledger(tgt))
    written = yaml.safe_load((tgt / "triggers/daily.yaml").read_text())
    assert written["enabled"] is False and written["cron"] == "0 9 * * *"


def test_apply_target_trigger_without_enabled_stays_active(tmp_path):
    # Law #1 + df-886.8: the engine runs a trigger without 'enabled' as active (TriggerModel
    # default), so updating its definition must keep it active, not write 'enabled: false'.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", _SOURCE_TRIGGER)
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\njobs: []\n")
    apply_plan(src, tgt, _plan(src, tgt, _TRIGGERS), Ledger(tgt))
    assert yaml.safe_load((tgt / "triggers/daily.yaml").read_text(encoding="utf-8"))["enabled"] is True


def test_apply_promoted_trigger_stays_readable_like_its_source(tmp_path):
    # The promoted file is what a person opens in Prod: keys keep the source order and
    # non-ASCII text stays literal, not \x-escaped.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml",
        "name: relatório\ncron: '0 9 * * *'\nenabled: true\njobs: []\nnote: manhã\n")
    apply_plan(src, tgt, _plan(src, tgt, _TRIGGERS), Ledger(tgt))
    text = (tgt / "triggers/daily.yaml").read_text(encoding="utf-8")
    assert "relatório" in text and "manhã" in text
    keys = [line.split(":", 1)[0] for line in text.splitlines()]
    assert keys == ["name", "cron", "enabled", "jobs", "note"]


def test_apply_entry_timestamp_is_utc(tmp_path):
    # Deploy history is compared across machines and with the scheduler, which run on UTC.
    from datetime import datetime, timedelta
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    entry = apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), Ledger(tgt))
    assert datetime.fromisoformat(entry.ts).utcoffset() == timedelta(0)


_ENCODING_PROBE = r'''
import sys, warnings
from pathlib import Path
from dataforge.deploy.apply import apply_plan
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.models import PairRule, PipelineModel
from dataforge.deploy.pairing import resolve_pairs
from dataforge.deploy.plan import build_plan
src, tgt = Path(sys.argv[1], "Dev"), Path(sys.argv[1], "Prod")
p = PipelineModel(target="Prod", pairs=[PairRule(source="triggers/*.yaml", target="triggers/")])
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
apply_plan(src, tgt, plan, Ledger(tgt))
'''


def test_apply_promotes_utf8_trigger_text_on_a_non_utf8_machine(tmp_path):
    # Workspace files are UTF-8 whatever the machine's locale. Under a non-UTF-8 locale the
    # planner crashes on an accented trigger (ASCII), or on Windows (cp1252) decodes it as
    # mojibake that apply then writes to Prod. Simulated here with the C locale.
    import os
    import subprocess
    import sys
    import dataforge
    _mk(tmp_path / "Dev", "triggers/daily.yaml", "")
    (tmp_path / "Dev/triggers/daily.yaml").write_bytes(
        "name: relatório\ncron: '0 9 * * *'\nenabled: true\njobs: []\n".encode("utf-8"))
    (tmp_path / "Prod").mkdir()
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
           "PYTHONPATH": str(Path(dataforge.__file__).parent.parent)}
    proc = subprocess.run([sys.executable, "-X", "utf8=0", "-c", _ENCODING_PROBE, str(tmp_path)],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    written = (tmp_path / "Prod/triggers/daily.yaml").read_bytes().decode("utf-8")
    assert "relatório" in written


def test_apply_with_nested_pair_bases_deletes_once_and_finishes(tmp_path):
    # df-886.15: the same DELETE listed twice made the second unlink raise FileNotFoundError.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "jobs/a.yaml", "a\n")
    _mk(src, "jobs/sub/k.yaml", "k\n")
    _mk(tgt, "jobs/sub/old.yaml", "old\n")
    plan = _plan(src, tgt, "target: Prod\nprune: true\npairs:\n"
                           "  - {source: 'jobs/**', target: 'jobs'}\n"
                           "  - {source: 'jobs/sub/**', target: 'jobs/sub'}\n")
    apply_plan(src, tgt, plan, Ledger(tgt))   # must not raise
    assert not (tgt / "jobs/sub/old.yaml").exists()
    assert (tgt / "jobs/sub/k.yaml").exists()


@pytest.mark.parametrize("dirty", ["x: !!bool abc\n", "x: !!timestamp abc\n", "[" * 5000 + "]" * 5000],
                         ids=["bad-bool-tag", "bad-timestamp-tag", "deep-nesting"])
def test_apply_trigger_with_a_broken_yaml_tag_is_copied_verbatim(tmp_path, dirty):
    # df-886.9: constructor errors (KeyError, AttributeError, RecursionError) degrade, never crash.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/bad.yaml", dirty)
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    apply_plan(src, tgt, plan, Ledger(tgt))
    assert (tgt / "triggers/bad.yaml").read_text() == dirty


@pytest.mark.parametrize("dirty", ['a: !!int ""\n', "a: " + "[" * 350 + "]" * 350 + "\n",
                                   "a: 0x" + "f" * 4000 + "\n"],
                         ids=["int-tag-empty", "dump-recursion", "huge-hex-int"])
def test_apply_trigger_that_cannot_be_redumped_is_copied_verbatim(tmp_path, dirty):
    # df-886.17: a trigger that loads but can't be dumped again is copied as it is.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/bad.yaml", dirty)
    plan = _plan(src, tgt, "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    apply_plan(src, tgt, plan, Ledger(tgt))
    assert (tgt / "triggers/bad.yaml").read_text() == dirty


# --- df-ocwj: the deploy keeps the on/off status the ENGINE reads, not Python's bool() ---

@pytest.mark.parametrize("enabled,engine_reads", [
    ("'no'", False), ("'false'", False), ("'off'", False), ("0", False),
    ("'yes'", True), ("1", True), ("true", True),
])
def test_apply_keeps_the_status_the_engine_reads_from_the_target(tmp_path, enabled, engine_reads):
    from dataforge.models import TriggerModel
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", _SOURCE_TRIGGER)
    _mk(tgt, "triggers/daily.yaml", f"name: daily\ncron: '0 5 * * *'\nenabled: {enabled}\n")
    assert TriggerModel(**yaml.safe_load((tgt / "triggers/daily.yaml").read_text())).enabled is engine_reads
    apply_plan(src, tgt, _plan(src, tgt, _TRIGGERS), Ledger(tgt))
    written = yaml.safe_load((tgt / "triggers/daily.yaml").read_text(encoding="utf-8"))
    assert written["enabled"] is engine_reads and written["cron"] == "0 9 * * *"


def test_apply_target_trigger_with_an_unreadable_status_is_written_disarmed(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", _SOURCE_TRIGGER)
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: null\n")
    apply_plan(src, tgt, _plan(src, tgt, _TRIGGERS), Ledger(tgt))
    assert yaml.safe_load((tgt / "triggers/daily.yaml").read_text(encoding="utf-8"))["enabled"] is False


# --- df-m1dk: apply never writes or deletes outside the target, whatever plan it is handed ---

_HOSTILE = ["../escaped.txt", "scripts/../../escaped.txt", "/abs/escaped.txt", "scripts\\..\\..\\escaped.txt"]


@pytest.mark.parametrize("action", [Action.add, Action.modify, Action.delete])
@pytest.mark.parametrize("hostile", _HOSTILE)
def test_apply_refuses_a_target_path_outside_the_target_before_touching_anything(
        tmp_path, hostile, action):
    from dataforge.deploy.models import DeployPlan, ItemChange
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    _mk(tgt, "scripts/ok.py", "old\n")
    (tmp_path / "escaped.txt").write_text("precious")
    hostile = hostile.replace("/abs", str(tmp_path)) if hostile.startswith("/abs") else hostile
    plan = DeployPlan(source="Dev", target="Prod", pipeline="p", items=[
        ItemChange(source_path="scripts/a.py", target_path="scripts/ok.py",
                   kind=Kind.script, action=Action.modify),          # a good item goes first
        ItemChange(source_path=None if action == Action.delete else "scripts/a.py",
                   target_path=hostile, kind=Kind.script, action=action),
    ])
    led = Ledger(tgt)
    with pytest.raises(ValueError, match="outside the target workspace|escapes target workspace"):
        apply_plan(src, tgt, plan, led)
    assert (tmp_path / "escaped.txt").read_text() == "precious"
    assert (tgt / "scripts/ok.py").read_text() == "old\n"   # pre-flight: nothing applied
    assert led.last() is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks; junctions go in the Windows smoke")
def test_apply_refuses_a_folder_symlinked_out_of_the_target(tmp_path):
    from dataforge.deploy.models import DeployPlan, ItemChange
    src, tgt, outside = tmp_path / "Dev", tmp_path / "Prod", tmp_path / "outside"
    _mk(src, "scripts/a.py", "print(1)\n")
    outside.mkdir()
    tgt.mkdir()
    os.symlink(outside, tgt / "scripts", target_is_directory=True)
    plan = DeployPlan(source="Dev", target="Prod", pipeline="p", items=[
        ItemChange(source_path="scripts/a.py", target_path="scripts/a.py",
                   kind=Kind.script, action=Action.add)])
    with pytest.raises(ValueError, match="outside the target workspace"):
        apply_plan(src, tgt, plan, Ledger(tgt))
    assert not any(outside.iterdir())
