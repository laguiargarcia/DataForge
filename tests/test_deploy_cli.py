import re
from pathlib import Path
from typer.testing import CliRunner
from dataforge.cli import app
from dataforge.deploy.ledger import Ledger

runner = CliRunner()
# rich highlights numbers when color is forced (FORCE_COLOR, CI), splitting 'applied 1' (df-pva).
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


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


def test_diff_shows_add(dataforge_home):
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "diff", "to-prod", "-w", "Dev"])
    assert r.exit_code == 0
    assert "ADD" in r.stdout
    assert "scripts/a.py" in r.stdout


def test_diff_unknown_pipeline_errors(dataforge_home):
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "diff", "nope", "-w", "Dev"])
    assert r.exit_code == 1


def test_diff_unknown_workspace_errors(dataforge_home):
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "diff", "to-prod", "-w", "Ghost"])
    assert r.exit_code == 1


def test_apply_yes_writes_target(dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])
    assert r.exit_code == 0
    assert (prod / "scripts/a.py").read_text() == "print(1)\n"
    assert Ledger(prod).last() is not None


def test_apply_aborts_without_confirmation(dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev"], input="n\n")
    assert r.exit_code != 0                       # typer.confirm(abort=True) -> non-zero
    assert not (prod / "scripts/a.py").exists()   # nothing written


def test_apply_only_filters_to_matching_targets(dataforge_home):
    dev, prod = _setup(dataforge_home)
    _mk(dev, "scripts/b.py", "print(2)\n")
    r = runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--only", "scripts/a.py", "--yes"])
    assert r.exit_code == 0
    assert (prod / "scripts/a.py").exists()
    assert not (prod / "scripts/b.py").exists()   # filtered out


def test_apply_nothing_to_do_is_clean_exit(dataforge_home):
    dev, prod = _setup(dataforge_home)
    runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])      # first deploy
    r = runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])  # nothing changed
    assert r.exit_code == 0
    assert "Nothing to apply" in r.stdout


def test_rollback_undoes_last_deploy(dataforge_home):
    dev, prod = _setup(dataforge_home)
    runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])
    assert (prod / "scripts/a.py").exists()
    r = runner.invoke(app, ["deploy", "rollback", "to-prod", "-w", "Dev", "--yes"])
    assert r.exit_code == 0
    assert not (prod / "scripts/a.py").exists()


def test_rollback_with_no_history_is_clean(dataforge_home):
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "rollback", "to-prod", "-w", "Dev", "--yes"])
    assert r.exit_code == 0
    assert "No deploy to roll back" in r.stdout


def test_status_pending_then_up_to_date(dataforge_home):
    dev, prod = _setup(dataforge_home)
    r1 = runner.invoke(app, ["deploy", "status", "to-prod", "-w", "Dev"])
    assert r1.exit_code == 0
    assert "Pending" in r1.stdout
    runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])
    r2 = runner.invoke(app, ["deploy", "status", "to-prod", "-w", "Dev"])
    assert "Up to date" in r2.stdout


def test_run_deploys_non_interactively(dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])   # NO input — must not prompt
    assert r.exit_code == 0
    assert (prod / "scripts/a.py").read_text() == "print(1)\n"
    assert "applied 1" in _ANSI.sub("", r.stdout)


def test_run_is_idempotent_exit_zero(dataforge_home):
    dev, prod = _setup(dataforge_home)
    runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])   # nothing new
    assert r.exit_code == 0                                              # idempotent success
    assert "nothing to apply" in r.stdout.lower()


def test_run_json_is_machine_readable(dataforge_home):
    import json
    dev, prod = _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["status"] == "APPLIED"
    assert payload["applied"] == 1
    assert payload["entry_id"]


def test_run_unknown_pipeline_exits_one_clean(dataforge_home):
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "run", "nope", "-w", "Dev"])
    assert r.exit_code == 1
    assert "Traceback" not in (r.output or "")
    assert r.output.strip()


def test_run_write_failure_exits_one_clean(dataforge_home, monkeypatch):
    dev, prod = _setup(dataforge_home)
    from dataforge.deploy import service as svc
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(svc, "apply_plan", boom)
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    assert r.exit_code == 1
    assert "rollback" in r.output.lower() and "to-prod" in r.output   # hint names the pipeline
    assert not isinstance(r.exception, OSError)       # OSError handled, never leaked


def test_run_updates_active_trigger_preserving_status(dataforge_home):
    dev = _ws(dataforge_home, "Dev"); prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    assert r.exit_code == 0
    written = (prod / "triggers/daily.yaml").read_text()
    assert "0 9 * * *" in written        # law #2': definition promoted to source's cron
    assert "enabled: true" in written     # status preserved — still active


class _FakeResp:
    def raise_for_status(self):
        pass


class _FakeClient:
    """Records the URLs a deploy POSTs to the daemon's trigger-reconcile endpoint."""
    posted: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, *a, **k):
        _FakeClient.posted.append(url)
        return _FakeResp()


def test_run_reconciles_active_trigger_via_daemon(dataforge_home, monkeypatch):
    dev = _ws(dataforge_home, "Dev"); prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")
    monkeypatch.setattr(_FakeClient, "posted", [])
    monkeypatch.setattr("dataforge.deploy.cli.httpx.Client", _FakeClient)
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    assert r.exit_code == 0
    assert any(u.endswith("/api/workspaces/Prod/triggers/daily/reconcile") for u in _FakeClient.posted)


def test_run_warns_when_daemon_unreachable(dataforge_home, monkeypatch):
    dev = _ws(dataforge_home, "Dev"); prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")

    class _Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): raise RuntimeError("connection refused")

    monkeypatch.setattr("dataforge.deploy.cli.httpx.Client", _Boom)
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    assert r.exit_code == 0                                  # deploy still succeeds — files landed
    assert "reconciled" in r.output.lower() or "re-save" in r.output.lower()


def test_diff_malformed_pipeline_errors_cleanly(dataforge_home):
    dev, prod = _setup(dataforge_home)
    _mk(dev, "deploy/bad.yaml", "target: Prod\npairs: [oops\n")  # invalid YAML syntax
    r = runner.invoke(app, ["deploy", "diff", "bad", "-w", "Dev"])
    assert r.exit_code == 1
    assert "invalid" in r.stdout.lower()


def test_diff_pipeline_missing_target_errors_cleanly(dataforge_home):
    dev, prod = _setup(dataforge_home)
    _mk(dev, "deploy/notarget.yaml", "pairs: []\n")  # missing required 'target'
    r = runner.invoke(app, ["deploy", "diff", "notarget", "-w", "Dev"])
    assert r.exit_code == 1


def test_apply_only_glob_pattern(dataforge_home):
    dev, prod = _setup(dataforge_home)
    _mk(dev, "scripts/b.txt", "data\n")
    r = runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--only", "*.py", "--yes"])
    assert r.exit_code == 0
    assert (prod / "scripts/a.py").exists()
    assert not (prod / "scripts/b.txt").exists()


def test_apply_only_no_match_is_clean(dataforge_home):
    dev, prod = _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--only", "nomatch/*", "--yes"])
    assert r.exit_code == 0
    assert "Nothing to apply" in r.stdout
    assert not (prod / "scripts/a.py").exists()


def test_status_unknown_pipeline_errors(dataforge_home):
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "status", "nope", "-w", "Dev"])
    assert r.exit_code == 1


def test_rollback_prompt_shows_provenance(dataforge_home):
    dev, prod = _setup(dataforge_home)
    runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])
    r = runner.invoke(app, ["deploy", "rollback", "to-prod", "-w", "Dev"], input="n\n")
    assert "Dev" in r.stdout and "Prod" in r.stdout and "to-prod" in r.stdout


def test_rollback_missing_blob_fails_clean(dataforge_home):
    import shutil
    dev = _ws(dataforge_home, "Dev")
    prod = _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print('new')\n")
    _mk(prod, "scripts/a.py", "print('old')\n")  # target exists -> MODIFY snapshots a before-blob
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    runner.invoke(app, ["deploy", "apply", "to-prod", "-w", "Dev", "--yes"])
    shutil.rmtree(prod / "deploy" / ".ledger" / "objects")  # simulate ledger corruption
    result = runner.invoke(app, ["deploy", "rollback", "to-prod", "-w", "Dev", "--yes"])
    assert result.exit_code == 1
    # The guard must convert the engine's FileNotFoundError into a clean, printed message.
    # Without the guard CliRunner still reports exit 1, but the error stays an uncaught
    # exception and never reaches stdout — these two assertions are the real regression check.
    assert "rollback aborted" in result.output
    assert not isinstance(result.exception, FileNotFoundError)


def test_diff_escaping_target_fails_clean(dataforge_home):
    dev = _ws(dataforge_home, "Dev")
    _ws(dataforge_home, "Prod")
    _mk(dev, "scripts/a.py", "print(1)\n")
    _mk(dev, "deploy/esc.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/a.py', target: '../../etc/'}\n")
    result = runner.invoke(app, ["deploy", "diff", "esc", "-w", "Dev"])
    assert result.exit_code == 1
    assert "Traceback" not in (result.output or "")
    assert result.output.strip()


def test_run_unexpected_error_exits_one_clean(dataforge_home, monkeypatch):
    dev, prod = _setup(dataforge_home)
    from dataforge.deploy import service as svc
    def boom(*a, **k):
        raise ValueError("kaboom")
    monkeypatch.setattr(svc, "apply_plan", boom)
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    assert r.exit_code == 1
    assert r.output.strip()                          # never a silent exit 1 on the headless path
    assert "Traceback" not in r.output
    assert not isinstance(r.exception, ValueError)   # handled, not leaked


def test_run_json_error_is_valid_json(dataforge_home):
    import json
    _setup(dataforge_home)
    r = runner.invoke(app, ["deploy", "run", "nope", "-w", "Dev", "--json"])
    assert r.exit_code == 1
    payload = json.loads(r.stdout.strip().splitlines()[-1])   # stdout stays parseable on error
    assert payload["status"] == "ERROR" and payload["kind"] == "not_found"


def test_run_json_nothing_to_apply_is_valid_json(dataforge_home):
    import json
    dev, prod = _setup(dataforge_home)
    runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])          # first deploy
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["status"] == "NOTHING_TO_APPLY" and payload["entry_id"] is None


def test_run_all_protected_message_not_up_to_date(dataforge_home):
    # PROTECTED now arises only from a prune that would delete an active target trigger.
    # df-gl6: prune requires a NON-EMPTY source set, so source carries an in-sync trigger (daily)
    # while the target also has a source-absent active trigger (extra) that must be PROTECTED.
    dev = _ws(dataforge_home, "Dev"); prod = _ws(dataforge_home, "Prod")
    _mk(dev, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(prod, "triggers/extra.yaml", "name: extra\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(dev, "deploy/to-prod.yaml",
        "target: Prod\nprune: true\npairs:\n  - {source: 'triggers/**', target: 'triggers/'}\n")
    r = runner.invoke(app, ["deploy", "run", "to-prod", "-w", "Dev"])
    assert r.exit_code == 0
    assert "PROTECTED" in r.stdout and "untouched" in r.stdout.lower()
    assert "up to date" not in r.stdout.lower()       # must not claim up-to-date when laws blocked it
