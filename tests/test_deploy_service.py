from pathlib import Path

import pytest

from dataforge.deploy import service
from dataforge.deploy.service import DeployServiceError


def _ws(home: Path, name: str) -> Path:
    ws = home / "workspaces" / name
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text(f"name: {name}\n")
    return ws


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _setup(home: Path):
    """Dev (source) → Prod (target) with one script + a to-prod pipeline."""
    dev = _ws(home, "Dev")
    prod = _ws(home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    return dev, prod


def test_resolve_unknown_workspace(dataforge_home):
    with pytest.raises(DeployServiceError) as ei:
        service.resolve("NoSuch", "to-prod")
    assert ei.value.kind == "not_found"


def test_resolve_missing_pipeline(dataforge_home):
    _ws(dataforge_home, "Dev")
    with pytest.raises(DeployServiceError) as ei:
        service.resolve("Dev", "to-prod")
    assert ei.value.kind == "not_found"


def test_resolve_invalid_pipeline(dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    _mk(dev, "deploy/bad.yaml", "target: [unclosed\n")  # malformed YAML
    with pytest.raises(DeployServiceError) as ei:
        service.resolve("Dev", "bad")
    assert ei.value.kind == "invalid"


def test_list_pipelines_excludes_non_yaml(dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    _mk(dev, "deploy/to-prod.yaml", "target: Prod\npairs: []\n")
    _mk(dev, "deploy/.deployignore", "*.duckdb\n")
    assert service.list_pipelines(dev) == ["to-prod"]


def test_build_produces_add(dataforge_home):
    dev, prod = _setup(dataforge_home)
    src, pl, tgt = service.resolve("Dev", "to-prod")
    plan = service.build(src, pl, "to-prod", tgt)
    assert any(i.target_path == "scripts/a.py" for i in plan.writable())


def test_build_rejects_escaping_target(dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/esc.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/a.py', target: '../../etc/'}\n")
    src, pl, tgt = service.resolve("Dev", "esc")
    with pytest.raises(DeployServiceError) as ei:
        service.build(src, pl, "esc", tgt)
    assert ei.value.kind == "invalid"


def test_apply_none_when_nothing_selected(dataforge_home):
    dev, prod = _setup(dataforge_home)
    src, pl, tgt = service.resolve("Dev", "to-prod")
    plan = service.build(src, pl, "to-prod", tgt)
    assert service.apply(src, tgt, plan, only=set()) is None  # empty selection → no entry


def test_apply_only_selects_subset(dataforge_home):
    """`only` writes exactly the selected target paths and leaves the rest untouched."""
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print('a')\n")
    _mk(dev, "scripts/b.py", "print('b')\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    src, pl, tgt = service.resolve("Dev", "to-prod")
    plan = service.build(src, pl, "to-prod", tgt)
    entry = service.apply(src, tgt, plan, only={"scripts/a.py"})
    assert entry is not None
    assert [i.target_path for i in entry.items] == ["scripts/a.py"]
    assert (prod / "scripts/a.py").exists()
    assert not (prod / "scripts/b.py").exists()  # not selected → not written


def test_apply_and_rollback_roundtrip(dataforge_home):
    dev, prod = _setup(dataforge_home)
    src, pl, tgt = service.resolve("Dev", "to-prod")
    plan = service.build(src, pl, "to-prod", tgt)
    entry = service.apply(src, tgt, plan, only=None)
    assert entry is not None
    assert (prod / "scripts/a.py").read_text() == "print(1)\n"
    last = service.last_entry(tgt)
    assert last is not None
    assert last.id == entry.id
    rolled = service.rollback(tgt)
    assert rolled.id == entry.id
    assert not (prod / "scripts/a.py").exists()  # ADD undone
    assert service.last_entry(tgt) is None


# --- auto_deploy ---
def test_auto_deploy_applies_and_returns_result(dataforge_home):
    dev = _ws(dataforge_home, "Dev"); _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    from dataforge.deploy import service
    from dataforge.deploy.models import AutoDeployStatus
    r = service.auto_deploy("Dev", "to-prod")
    assert r.status == AutoDeployStatus.applied
    assert r.applied == 1
    assert r.entry_id is not None
    assert r.total == 1
    assert (dataforge_home / "workspaces/Prod/scripts/a.py").read_text() == "print(1)\n"


def test_auto_deploy_idempotent_second_run_is_nothing(dataforge_home):
    dev = _ws(dataforge_home, "Dev"); _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    from dataforge.deploy import service
    from dataforge.deploy.models import AutoDeployStatus
    service.auto_deploy("Dev", "to-prod")
    r = service.auto_deploy("Dev", "to-prod")
    assert r.status == AutoDeployStatus.nothing_to_apply
    assert r.applied == 0
    assert r.entry_id is None


def test_auto_deploy_honors_law3_and_preserves_trigger_status(dataforge_home):
    dev = _ws(dataforge_home, "Dev"); prod = _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "scripts/secret.py", "TOKEN=1\n")
    _mk(dev, "deploy/.deployignore", "scripts/secret.py\n")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n"
        "  - {source: 'scripts/**', target: 'scripts/'}\n"
        "  - {source: 'triggers/**', target: 'triggers/'}\n")
    from dataforge.deploy import service
    r = service.auto_deploy("Dev", "to-prod")
    assert r.ignored >= 1                                # law #3: secret skipped
    assert not (prod / "scripts/secret.py").exists()     # ignored never written
    # law #2': the active trigger's definition is updated, but its on/off status is preserved
    written = (prod / "triggers/daily.yaml").read_text()
    assert "0 9 * * *" in written                        # definition promoted (cron updated)
    assert "enabled: true" in written                    # still active — status unchanged


def test_auto_deploy_unknown_pipeline_raises(dataforge_home):
    _ws(dataforge_home, "Dev")
    from dataforge.deploy import service
    from dataforge.deploy.service import DeployServiceError
    with pytest.raises(DeployServiceError):
        service.auto_deploy("Dev", "nope")


def test_auto_deploy_propagates_write_failure(dataforge_home, monkeypatch):
    dev = _ws(dataforge_home, "Dev"); _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    from dataforge.deploy import service
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(service, "apply_plan", boom)   # service.apply -> apply_plan
    with pytest.raises(OSError):
        service.auto_deploy("Dev", "to-prod")


def test_auto_deploy_protected_prune_is_nothing_to_apply(dataforge_home):
    # The only PROTECTED case left (law #2'): a prune that would DELETE an active target trigger
    # absent from the source. Nothing is writable, so the result is nothing_to_apply, protected=1.
    # df-gl6: prune now requires a NON-EMPTY source set, so the source carries an in-sync trigger
    # (daily) while the target also has an active source-absent trigger (extra) that must be PROTECTED.
    dev = _ws(dataforge_home, "Dev"); prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/extra.yaml", "name: extra\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\nprune: true\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")
    from dataforge.deploy import service
    from dataforge.deploy.models import AutoDeployStatus
    r = service.auto_deploy("Dev", "to-prod")
    assert r.status == AutoDeployStatus.nothing_to_apply
    assert r.protected == 1
    assert r.applied == 0
    assert r.entry_id is None
    assert (prod / "triggers/daily.yaml").read_text()    # in-sync trigger left intact (never deleted)
    assert (prod / "triggers/extra.yaml").read_text()    # live source-absent trigger PROTECTED, not deleted


def test_trigger_names_lists_only_triggers():
    from dataforge.deploy import service
    from dataforge.deploy.models import DeployEntry, ItemChange, Kind, Action
    entry = DeployEntry(id="x", ts="t", source="Dev", target="Prod", pipeline="p", items=[
        ItemChange(source_path="triggers/daily.yaml", target_path="triggers/daily.yaml",
                   kind=Kind.trigger, action=Action.modify),
        ItemChange(source_path="scripts/a.py", target_path="scripts/a.py",
                   kind=Kind.script, action=Action.add),
    ])
    assert service.trigger_names(entry) == ["daily"]   # stem of the trigger item only
    assert service.trigger_names(None) == []


def test_app_bundle_roundtrip_is_byte_exact(dataforge_home):
    # df-ve7: a binary dist/ asset survives apply byte-for-byte and rollback removes the app.
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'apps/**', target: 'apps/'}\n")
    _mk(dev, "apps/financas/app.yaml", "name: financas\n")
    _mk(dev, "apps/financas/dist/index.html", "<html></html>")
    blob = bytes(range(256)) * 4
    (dev / "apps/financas/dist/assets").mkdir(parents=True)
    (dev / "apps/financas/dist/assets/f.woff2").write_bytes(blob)
    service.auto_deploy("Dev", "to-prod")
    assert (prod / "apps/financas/dist/assets/f.woff2").read_bytes() == blob
    assert (prod / "apps/financas/app.yaml").exists()
    service.rollback(prod)
    assert not (prod / "apps").exists()
