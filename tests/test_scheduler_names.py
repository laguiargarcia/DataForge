"""Names that reach the OS scheduler (PowerShell, bash, crontab, launchd plist) are only ever
letters, digits, '-' and '_' (df-utq8). Before, a trigger named "x'; Start-Process calc; '" broke
out of the PowerShell literal when the daemon registered it, and 'a;Start-Process calc' ran when
the task fired on native Windows; the deploy reconciled triggers by file name with no check at all.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HOSTILE = [
    "x'; Start-Process calc; '",   # leaves the PowerShell single-quoted literal
    "a;Start-Process calc",        # unquoted in the native-Windows task action
    "a$(calc)",
    'a"b',
    "a b",
    "daily\n",                     # '$' in ^...$ accepted a trailing newline
    "../x",
    "",
]
SAFE = "daily_run-2"


def _sinks():
    """Every function that turns a workspace/trigger/job name into an OS scheduler command."""
    from dataforge import cli
    return {
        "register_windows_trigger": lambda ws, n: cli._register_windows_trigger(
            ws, n, "0 9 * * *", "dataforge", "C:\\DataForge"),
        "deregister_windows_trigger": lambda ws, n: cli._deregister_windows_trigger(ws, n),
        "register_windows_task": lambda ws, n: cli._register_windows_task(ws, n, "0 9 * * *"),
        "register_crontab": lambda ws, n: cli._register_crontab(ws, n, "0 9 * * *", "true"),
        "deregister_crontab": lambda ws, n: cli._deregister_crontab(ws, n),
        "register_launchd": lambda ws, n: cli._register_launchd(
            ws, n, "0 9 * * *", "dataforge", "/home/x"),
        "deregister_launchd": lambda ws, n: cli._deregister_launchd(ws, n),
        "platform_register_trigger": lambda ws, n: cli._platform_register_trigger(ws, n, "0 9 * * *"),
        "platform_deregister_trigger": lambda ws, n: cli._platform_deregister_trigger(ws, n),
    }


@pytest.mark.parametrize("sink", list(_sinks()))
@pytest.mark.parametrize("bad", HOSTILE)
@pytest.mark.parametrize("which", ["workspace", "name"])
def test_no_scheduler_command_is_built_from_an_unsafe_name(sink, bad, which, tmp_path):
    ws, name = (bad, SAFE) if which == "workspace" else (SAFE, bad)
    with patch("dataforge.cli.subprocess.run") as run, \
         patch("dataforge.cli._is_wsl", return_value=False), \
         patch("dataforge.cli.Path.home", return_value=tmp_path):
        run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        build = _sinks()[sink]
        with pytest.raises(ValueError, match="OS scheduler"):
            build(ws, name)
    run.assert_not_called()
    assert not any(tmp_path.rglob("*.plist"))


def test_a_safe_name_still_reaches_the_scheduler():
    from dataforge.cli import _register_windows_trigger
    with patch("dataforge.cli.subprocess.run") as run, patch("dataforge.cli._is_wsl", return_value=False):
        run.return_value = MagicMock(returncode=0, stderr="")
        _register_windows_trigger("Prod", SAFE, "0 9 * * *", "dataforge", "C:\\DataForge")
    assert f"DataForge\\Prod\\trigger\\{SAFE}" in run.call_args[0][0][-1]


def test_a_workspace_name_with_a_trailing_newline_is_invalid(tmp_path, monkeypatch):
    from dataforge.compat import resolve_workspace
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws = tmp_path / "workspaces" / "Prod\n"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: Prod\n")   # exists on disk: only the name check stops it
    with pytest.raises(ValueError, match="Invalid workspace name"):
        resolve_workspace("Prod\n")


@pytest.mark.parametrize("bad", ["daily\n", "x'; Start-Process calc; '"])
def test_the_api_rejects_a_trigger_name_with_a_trailing_newline(test_client, workspace_dir, bad):
    payload = {"name": bad, "cron": "0 6 * * *", "enabled": True, "jobs": []}
    assert test_client.post("/api/workspaces/test_ws/triggers", json=payload).status_code == 400


def _hostile_trigger(ws: Path, name: str) -> None:
    (ws / "triggers").mkdir(parents=True, exist_ok=True)
    (ws / "triggers" / f"{name}.yaml").write_text(
        f"name: \"{name}\"\ncron: '0 9 * * *'\nenabled: true\njobs: []\n", encoding="utf-8")


@pytest.mark.parametrize("bad", ["x'; Start-Process calc; '", "a;Start-Process calc"])
def test_reconcile_never_hands_an_unsafe_trigger_name_to_the_scheduler(dataforge_home, bad):
    from dataforge.api.routers import triggers
    ws = dataforge_home / "workspaces" / "Prod"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: Prod\n")
    _hostile_trigger(ws, bad)
    warning = triggers.reconcile_os_trigger("Prod", bad)
    assert warning is not None
    assert "invalid trigger name" in warning
    triggers._platform_register_trigger.assert_not_called()      # conftest's MagicMock
    triggers._platform_deregister_trigger.assert_not_called()


def test_a_deploy_does_not_register_a_trigger_named_by_a_hostile_file(test_client, dataforge_home):
    """The real path: a Dev trigger file whose name is a PowerShell payload, deployed to Prod."""
    from dataforge.api.routers import triggers
    bad = "x'; Start-Process calc; '"
    dev, prod = (dataforge_home / "workspaces" / n for n in ("Dev", "Prod"))
    for ws in (dev, prod):
        ws.mkdir(parents=True)
        (ws / "workspace.yaml").write_text(f"name: {ws.name}\n")
    _hostile_trigger(dev, bad)
    (dev / "deploy").mkdir()
    (dev / "deploy" / "to-prod.yaml").write_text(
        "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    r = test_client.post("/api/workspaces/Dev/deploy/to-prod/apply", json={})
    assert r.status_code == 200
    assert (prod / "triggers" / f"{bad}.yaml").exists()           # the file itself is deployed
    for call in triggers._platform_register_trigger.call_args_list:
        assert bad not in call.args
    triggers._platform_deregister_trigger.assert_not_called()


def test_schedule_sync_skips_a_job_with_an_unsafe_name_and_registers_the_rest(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from dataforge import cli
    jobs = [SimpleNamespace(name="a;calc", schedule="0 9 * * *"),
            SimpleNamespace(name="nightly", schedule="0 2 * * *")]
    monkeypatch.setattr("dataforge.compat.list_workspaces", lambda: [tmp_path / "Prod"])
    monkeypatch.setattr("dataforge.parser.load_jobs", lambda _ws: jobs)
    monkeypatch.setattr(cli, "_upsert_and_get_enabled", lambda *a: True)
    with patch("dataforge.cli.subprocess.run") as run, patch("dataforge.cli._is_wsl", return_value=False):
        run.return_value = MagicMock(returncode=0, stderr="")
        cli.schedule_sync()
    scripts = [c.args[0][-1] for c in run.call_args_list]
    assert len(scripts) == 1
    assert "DataForge\\Prod\\nightly" in scripts[0]
