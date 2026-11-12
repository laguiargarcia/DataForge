from pathlib import Path


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
    dev = _ws(home, "Dev")
    prod = _ws(home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    return dev, prod


def test_list_pipelines_api(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = test_client.get("/api/workspaces/Dev/deploy/pipelines")
    assert r.status_code == 200
    assert r.json() == ["to-prod"]


def test_list_pipelines_unknown_ws(test_client, dataforge_home):
    r = test_client.get("/api/workspaces/Nope/deploy/pipelines")
    assert r.status_code == 404


def test_plan_api(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = test_client.get("/api/workspaces/Dev/deploy/to-prod/plan")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "Dev"
    assert body["target"] == "Prod"
    assert any(i["target_path"] == "scripts/a.py" for i in body["items"])


def test_plan_missing_pipeline_404(test_client, dataforge_home):
    _ws(dataforge_home, "Dev")
    r = test_client.get("/api/workspaces/Dev/deploy/to-prod/plan")
    assert r.status_code == 404


def test_plan_malformed_pipeline_400(test_client, dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    _mk(dev, "deploy/bad.yaml", "target: [unclosed\n")
    r = test_client.get("/api/workspaces/Dev/deploy/bad/plan")
    assert r.status_code == 400


def test_status_api_clean(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = test_client.get("/api/workspaces/Dev/deploy/to-prod/status")
    assert r.status_code == 200
    body = r.json()
    assert body["last"] is None
    assert body["pending"] == 1  # fixture has exactly one writable item (scripts/a.py)


def test_status_unknown_ws(test_client, dataforge_home):
    r = test_client.get("/api/workspaces/Nope/deploy/to-prod/status")
    assert r.status_code == 404


def test_apply_writes_target_and_returns_entry(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] is True
    assert (prod / "scripts/a.py").read_text() == "print(1)\n"
    assert len(body["entry"]["items"]) == 1


def test_apply_only_filters_subset(test_client, dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print('a')\n")
    _mk(dev, "scripts/b.py", "print('b')\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/apply",
                         json={"only": ["scripts/a.py"]})
    assert r.status_code == 200
    assert (prod / "scripts/a.py").exists()
    assert not (prod / "scripts/b.py").exists()  # not selected → not written


def test_apply_nothing_selected(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/apply",
                         json={"only": ["scripts/does-not-exist.py"]})
    assert r.status_code == 200
    assert r.json() == {"applied": False, "entry": None, "reconciled_triggers": []}
    assert not (prod / "scripts/a.py").exists()  # nothing selected → nothing written


def test_apply_updates_active_trigger_preserving_status(test_client, dataforge_home):
    """Law #2': an ACTIVE target trigger's DEFINITION is updated over HTTP, but the deploy never
    changes its on/off status (it stays enabled)."""
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/nightly.yaml", "name: nightly\ncron: '0 2 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/nightly.yaml",
        "name: nightly\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")  # active in target
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")
    plan = test_client.get("/api/workspaces/Dev/deploy/to-prod/plan").json()
    item = [i for i in plan["items"] if i["target_path"] == "triggers/nightly.yaml"]
    assert item
    assert item[0]["action"] == "MODIFY"
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/apply",
                         json={"only": ["triggers/nightly.yaml"]})
    assert r.json()["applied"] is True
    assert r.json()["reconciled_triggers"] == ["nightly"]   # daemon re-synced the live OS schedule
    written = (prod / "triggers/nightly.yaml").read_text()
    assert "0 2 * * *" in written        # definition promoted from source
    assert "enabled: true" in written     # status preserved — still active


def test_reconcile_endpoint_registers_active_deregisters_inactive(test_client, dataforge_home):
    """POST /triggers/{name}/reconcile re-syncs the OS task with the on-disk definition."""
    from unittest.mock import patch
    dev = _ws(dataforge_home, "Dev")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    with patch("dataforge.api.routers.triggers._register_os_trigger") as reg, \
         patch("dataforge.api.routers.triggers._deregister_os_trigger") as dereg:
        r = test_client.post("/api/workspaces/Dev/triggers/daily/reconcile")
        assert r.status_code == 200
        assert r.json()["reconciled"] is True
        reg.assert_called_once()       # enabled on disk -> register the OS task
        dereg.assert_not_called()
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: false\njobs: []\n")
    with patch("dataforge.api.routers.triggers._register_os_trigger") as reg, \
         patch("dataforge.api.routers.triggers._deregister_os_trigger") as dereg:
        test_client.post("/api/workspaces/Dev/triggers/daily/reconcile")
        reg.assert_not_called()
        dereg.assert_called_once()     # disabled -> ensure no stale live task


def test_rollback_restores_and_409_when_empty(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    assert test_client.post("/api/workspaces/Dev/deploy/to-prod/rollback").status_code == 409
    test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={})
    assert (prod / "scripts/a.py").exists()
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/rollback")
    assert r.status_code == 200
    assert not (prod / "scripts/a.py").exists()  # ADD undone
    assert test_client.post("/api/workspaces/Dev/deploy/to-prod/rollback").status_code == 409


def test_rollback_unknown_ws(test_client, dataforge_home):
    r = test_client.post("/api/workspaces/Nope/deploy/to-prod/rollback")
    assert r.status_code == 404


def test_apply_trigger_modify_keeps_target_enabled(test_client, dataforge_home):
    """Law #1: deploying to an INACTIVE target trigger promotes the definition but keeps the
    target's enabled flag (operational state is never promoted)."""
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/nightly.yaml", "name: nightly\ncron: '0 2 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/nightly.yaml",
        "name: nightly\ncron: '0 9 * * *'\nenabled: false\njobs: []\n")  # inactive target -> MODIFY allowed
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={})
    assert r.json()["applied"] is True
    written = (prod / "triggers/nightly.yaml").read_text()
    assert "0 2 * * *" in written          # source definition (cron) promoted
    assert "enabled: false" in written     # target's enabled preserved, NOT promoted to true


def test_apply_never_writes_ignored_path(test_client, dataforge_home):
    """Law #3: a .deployignore-matched path is never written even if a pair rule names it."""
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "scripts/secret.env", "TOKEN=xyz\n")
    _mk(dev, "deploy/.deployignore", "*.env\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    plan = test_client.get("/api/workspaces/Dev/deploy/to-prod/plan").json()
    ign = [i for i in plan["items"] if i["target_path"] == "scripts/secret.env"]
    assert ign
    assert ign[0]["action"] == "SKIP_IGNORED"
    test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={"only": ["scripts/secret.env"]})
    assert not (prod / "scripts/secret.env").exists()        # cannot force an ignored path
    test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={})
    assert (prod / "scripts/a.py").exists()
    assert not (prod / "scripts/secret.env").exists()        # full apply still skips it


def test_plan_escaping_target_400(test_client, dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/esc.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/a.py', target: '../../etc/'}\n")
    assert test_client.get("/api/workspaces/Dev/deploy/esc/plan").status_code == 400


def test_rollback_missing_blob_500(test_client, dataforge_home):
    import shutil
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print('new')\n")
    _mk(prod, "scripts/a.py", "print('old')\n")  # MODIFY -> snapshots a before-blob
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    assert test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={}).json()["applied"] is True
    shutil.rmtree(prod / "deploy" / ".ledger" / "objects")   # simulate ledger corruption
    assert test_client.post("/api/workspaces/Dev/deploy/to-prod/rollback").status_code == 500


def test_full_deploy_lifecycle(test_client, dataforge_home):
    dev, prod = _setup(dataforge_home)
    # list → plan → apply → status → rollback, all through the app
    assert test_client.get("/api/workspaces/Dev/deploy/pipelines").json() == ["to-prod"]
    assert test_client.get("/api/workspaces/Dev/deploy/to-prod/plan").json()["target"] == "Prod"
    assert test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={}).json()["applied"] is True
    status = test_client.get("/api/workspaces/Dev/deploy/to-prod/status").json()
    assert status["last"] is not None
    assert status["pending"] == 0
    assert test_client.post("/api/workspaces/Dev/deploy/to-prod/rollback").status_code == 200
    assert test_client.get("/api/workspaces/Dev/deploy/to-prod/status").json()["last"] is None
