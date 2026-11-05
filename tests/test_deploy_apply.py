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
