from pathlib import Path

import pytest



def write_trigger_yaml(workspace_dir: Path, name: str, content: str) -> None:
    """Write a trigger YAML file into workspace_dir/triggers/."""
    triggers_dir = workspace_dir / "triggers"
    triggers_dir.mkdir(exist_ok=True)
    (triggers_dir / f"{name}.yaml").write_text(content)


# ---------------------------------------------------------------------------
# SCHED-01: Parser unit tests
# ---------------------------------------------------------------------------

def test_load_trigger(workspace_dir):
    """load_trigger reads a trigger YAML file into TriggerModel."""
    write_trigger_yaml(
        workspace_dir,
        "daily",
        "name: daily\ncron: '0 6 * * *'\nenabled: true\njobs: []\n",
    )
    from dataforge.parser import load_trigger
    t = load_trigger(workspace_dir, "daily")
    assert t.name == "daily"
    assert t.cron == "0 6 * * *"
    assert t.enabled is True
    assert t.jobs == []


def test_load_triggers(workspace_dir):
    """load_triggers returns all triggers sorted by name."""
    write_trigger_yaml(
        workspace_dir,
        "weekly",
        "name: weekly\ncron: '0 9 * * 1'\nenabled: true\njobs: []\n",
    )
    write_trigger_yaml(
        workspace_dir,
        "daily",
        "name: daily\ncron: '0 6 * * *'\nenabled: false\njobs: []\n",
    )
    from dataforge.parser import load_triggers
    triggers = load_triggers(workspace_dir)
    assert len(triggers) == 2
    assert triggers[0].name == "daily"
    assert triggers[1].name == "weekly"


def test_save_trigger(workspace_dir):
    """save_trigger creates triggers/ dir if absent and writes parseable YAML."""
    from dataforge.models import TriggerModel
    from dataforge.parser import save_trigger, load_trigger
    trigger = TriggerModel(name="nightly", cron="0 2 * * *", enabled=True, jobs=[])
    # triggers/ does not exist yet — save_trigger must create it
    save_trigger(workspace_dir, trigger)
    trigger_file = workspace_dir / "triggers" / "nightly.yaml"
    assert trigger_file.exists()
    # Round-trip: reload and compare
    loaded = load_trigger(workspace_dir, "nightly")
    assert loaded.name == "nightly"
    assert loaded.cron == "0 2 * * *"
    assert loaded.enabled is True


def test_delete_trigger(workspace_dir):
    """delete_trigger removes the file; calling again does not raise."""
    write_trigger_yaml(
        workspace_dir,
        "temp",
        "name: temp\ncron: '* * * * *'\nenabled: true\njobs: []\n",
    )
    from dataforge.parser import delete_trigger
    trigger_file = workspace_dir / "triggers" / "temp.yaml"
    assert trigger_file.exists()
    delete_trigger(workspace_dir, "temp")
    assert not trigger_file.exists()
    # Second call must not raise (missing_ok)
    delete_trigger(workspace_dir, "temp")


# ---------------------------------------------------------------------------
# SCHED-01: API integration tests
# ---------------------------------------------------------------------------

def test_list_triggers_api(test_client, workspace_dir):
    """GET /api/workspaces/test_ws/triggers returns 200 and a JSON list."""
    response = test_client.get("/api/workspaces/test_ws/triggers")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_trigger_api(test_client, workspace_dir):
    """POST /api/workspaces/test_ws/triggers creates trigger YAML on disk."""
    payload = {
        "name": "api_trigger",
        "cron": "0 6 * * *",
        "enabled": True,
        "jobs": [],
    }
    response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)
    assert response.status_code == 201
    trigger_file = workspace_dir / "triggers" / "api_trigger.yaml"
    assert trigger_file.exists()


def test_delete_trigger_api(test_client, workspace_dir):
    """DELETE /api/workspaces/test_ws/triggers/{name} removes the YAML file."""
    write_trigger_yaml(
        workspace_dir,
        "to_delete",
        "name: to_delete\ncron: '0 5 * * *'\nenabled: true\njobs: []\n",
    )
    response = test_client.delete("/api/workspaces/test_ws/triggers/to_delete")
    assert response.status_code == 204
    assert not (workspace_dir / "triggers" / "to_delete.yaml").exists()


def test_create_trigger_rejects_bad_name(test_client, workspace_dir):
    """POST with name containing / or .. returns 400."""
    for bad_name in ["../evil", "a/b", "foo/../bar"]:
        payload = {"name": bad_name, "cron": "0 6 * * *", "enabled": True, "jobs": []}
        response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)
        assert response.status_code == 400, f"Expected 400 for name={bad_name!r}, got {response.status_code}"


def test_create_trigger_rejects_bad_cron(test_client, workspace_dir):
    """POST with invalid cron string returns 400."""
    payload = {"name": "valid_name", "cron": "not a cron", "enabled": True, "jobs": []}
    response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# SCHED-03: scheduled-run CLI command tests (Task 1 — TDD RED)
# ---------------------------------------------------------------------------

import yaml as _yaml
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from dataforge.cli import app

_runner = CliRunner()


def _write_trigger_with_jobs(workspace_dir, name, cron, enabled, jobs):
    """Write a trigger YAML with job entries."""
    triggers_dir = workspace_dir / "triggers"
    triggers_dir.mkdir(exist_ok=True)
    data = {
        "name": name,
        "cron": cron,
        "enabled": enabled,
        "jobs": jobs,
    }
    (triggers_dir / f"{name}.yaml").write_text(_yaml.dump(data, allow_unicode=True))


def test_scheduled_run_creates_job_runs(dataforge_home, workspace_dir, simple_job_dir):
    """scheduled-run with 2 jobs → 2 JobRunORM rows written, exit_code 0."""
    from dataforge.db import init_db, JobRunORM
    from sqlalchemy.orm import Session

    _write_trigger_with_jobs(
        workspace_dir,
        "daily",
        "0 6 * * *",
        True,
        [
            {"workspace": "test_ws", "job": "test_job", "params": {}},
            {"workspace": "test_ws", "job": "test_job", "params": {}},
        ],
    )

    result = _runner.invoke(app, ["scheduled-run", "test_ws", "daily"])
    assert result.exit_code == 0, (
        f"exit_code={result.exit_code}\n{result.output}\n{result.exception}"
    )

    from dataforge.compat import db_url as make_db_url
    global_db_url = make_db_url(dataforge_home / "dataforge.db")
    engine = init_db(global_db_url)
    with Session(engine) as session:
        rows = session.query(JobRunORM).filter_by(workspace="test_ws", job="test_job").all()
    assert len(rows) >= 2, f"Expected >= 2 JobRunORM rows, got {len(rows)}"


def test_scheduled_run_disabled_trigger(dataforge_home, workspace_dir):
    """A disabled trigger exits 0 without calling run_job."""
    _write_trigger_with_jobs(workspace_dir, "disabled", "0 6 * * *", False, [])

    with patch("dataforge.engine.run_job") as mock_run:
        result = _runner.invoke(app, ["scheduled-run", "test_ws", "disabled"])
    assert result.exit_code == 0
    mock_run.assert_not_called()


def test_scheduled_run_daemon_notify_failure_swallowed(dataforge_home, workspace_dir):
    """httpx.post raises → command still exits 0, no traceback printed."""
    _write_trigger_with_jobs(workspace_dir, "notify_test", "0 6 * * *", True, [])

    with patch("httpx.post", side_effect=Exception("connection refused")):
        result = _runner.invoke(app, ["scheduled-run", "test_ws", "notify_test"])
    assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
    assert "Traceback" not in (result.output or "")


# ---------------------------------------------------------------------------
# SCHED-02: OS trigger registration tests (Task 2 — TDD RED)
# ---------------------------------------------------------------------------

def test_register_crontab_trigger():
    """_register_crontab writes a crontab line with DataForge marker."""
    from dataforge.cli import _register_crontab

    existing_cron = "0 5 * * * /some/existing/command\n"
    cmd = "DATAFORGE_HOME=/home/user dataforge scheduled-run myws mytrigger"

    with patch("dataforge.cli.subprocess.run") as mock_run:
        # First call: crontab -l returns existing content
        mock_run.return_value = MagicMock(returncode=0, stdout=existing_cron, stderr="")
        _register_crontab("myws", "mytrigger", "0 6 * * *", cmd)

    assert mock_run.call_count == 2
    # First call: crontab -l
    assert mock_run.call_args_list[0][0][0] == ["crontab", "-l"]
    # Second call: crontab - with new content
    new_crontab = mock_run.call_args_list[1][1]["input"]
    assert "# DataForge:myws:mytrigger" in new_crontab
    assert "0 6 * * *" in new_crontab
    assert cmd in new_crontab


def test_register_crontab_empty():
    """crontab -l returncode != 0 → treated as empty, no exception, new crontab written."""
    from dataforge.cli import _register_crontab

    cmd = "DATAFORGE_HOME=/home/user dataforge scheduled-run ws1 t1"
    with patch("dataforge.cli.subprocess.run") as mock_run:
        # First call: crontab -l returns non-zero (empty crontab)
        empty_result = MagicMock(returncode=1, stdout="", stderr="no crontab for user")
        written = MagicMock(returncode=0)
        mock_run.side_effect = [empty_result, written]
        # Should not raise
        _register_crontab("ws1", "t1", "0 6 * * *", cmd)

    assert mock_run.call_count == 2
    new_crontab = mock_run.call_args_list[1][1]["input"]
    assert "# DataForge:ws1:t1" in new_crontab


def test_deregister_then_register():
    """Deregister removes only the matching marker line; register re-adds exactly one."""
    from dataforge.cli import _register_crontab, _deregister_crontab

    marker_line = "0 6 * * * dataforge scheduled-run myws t1  # DataForge:myws:t1"
    existing = f"0 5 * * * /other/cmd\n{marker_line}\n"
    cmd = "dataforge scheduled-run myws t1"

    # Deregister: remove marker line
    written_after_deregister = []
    with patch("dataforge.cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=existing, stderr="")
        _deregister_crontab("myws", "t1")
        deregister_input = mock_run.call_args_list[1][1]["input"]
        assert "# DataForge:myws:t1" not in deregister_input
        assert "/other/cmd" in deregister_input

    # Register: add marker line back to clean crontab
    clean = "0 5 * * * /other/cmd\n"
    with patch("dataforge.cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=clean, stderr="")
        _register_crontab("myws", "t1", "0 6 * * *", cmd)
        register_input = mock_run.call_args_list[1][1]["input"]
        # Exactly one marker line
        assert register_input.count("# DataForge:myws:t1") == 1


def test_register_launchd_trigger(tmp_path):
    """_register_launchd writes plist with DATAFORGE_HOME and calls launchctl."""
    from dataforge.cli import _register_launchd

    launchagents_dir = tmp_path / "Library" / "LaunchAgents"
    launchagents_dir.mkdir(parents=True)

    with patch("dataforge.cli.subprocess.run") as mock_run, \
         patch("pathlib.Path.home", return_value=tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _register_launchd("myws", "mytrigger", "0 6 * * *", "dataforge", "/home/user")

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.dataforge.myws.mytrigger.plist"
    assert plist_path.exists(), f"plist not found at {plist_path}"
    content = plist_path.read_text()
    assert "DATAFORGE_HOME" in content
    assert "/home/user" in content
    assert "scheduled-run" in content
    assert "myws" in content
    assert "mytrigger" in content

    # launchctl should be called
    launchctl_calls = [c for c in mock_run.call_args_list if "launchctl" in str(c)]
    assert len(launchctl_calls) >= 1


def _launchd_interval(tmp_path, cron):
    """Register *cron* with launchd (subprocess and home mocked) and return the parsed
    StartCalendarInterval of the plist it wrote."""
    import plistlib
    from dataforge.cli import _register_launchd

    with patch("dataforge.cli.subprocess.run") as mock_run, \
         patch("pathlib.Path.home", return_value=tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _register_launchd("myws", "t1", cron, "dataforge", "/home/user")
    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.dataforge.myws.t1.plist"
    return plistlib.loads(plist_path.read_bytes())["StartCalendarInterval"]


@pytest.mark.parametrize(("cron", "expected"), [
    ("0 6 * * *", {"Minute": 0, "Hour": 6}),
    ("30 5 1 6 *", {"Minute": 30, "Hour": 5, "Day": 1, "Month": 6}),   # df-vo3x.1: Month kept
    ("0 6 * * 1", {"Minute": 0, "Hour": 6, "Weekday": 1}),
    ("0 6 15 * 1", {"Minute": 0, "Hour": 6, "Day": 15, "Weekday": 1}),  # both kept: launchd ORs them, like cron
    ("59 23 31 12 7", {"Minute": 59, "Hour": 23, "Day": 31, "Month": 12, "Weekday": 7}),
])
def test_register_launchd_keeps_every_cron_field(tmp_path, cron, expected):
    assert _launchd_interval(tmp_path, cron) == expected


@pytest.mark.parametrize("cron", [
    "*/5 * * * *",   # step
    "0 6 1,15 * *",  # list
    "0 6 * * 1-5",   # range
    "0 6 * * MON",   # name
    "60 6 * * *",    # minute out of range
    "0 24 * * *",    # hour out of range
    "0 6 0 * *",     # day out of range
    "0 6 * 13 *",    # month out of range
    "0 6 * * 8",     # weekday out of range
    "0 6 * *",       # 4 fields
])
def test_register_launchd_rejects_what_the_plist_cannot_express(tmp_path, cron):
    """A cron the plist cannot represent raises before anything is written or loaded, instead of
    silently dropping the field and firing more often than asked (df-vo3x.1)."""
    from dataforge.cli import _register_launchd

    with patch("dataforge.cli.subprocess.run") as mock_run, \
         patch("pathlib.Path.home", return_value=tmp_path), \
         pytest.raises(ValueError, match="cron"):
        _register_launchd("myws", "t1", cron, "dataforge", "/home/user")
    mock_run.assert_not_called()
    assert not (tmp_path / "Library").exists()


def test_register_windows_trigger():
    """_register_windows_trigger uses correct task name and argument list (no shell=True)."""
    from dataforge.cli import _register_windows_trigger

    with patch("dataforge.cli.subprocess.run") as mock_run, \
         patch("dataforge.cli._is_wsl", return_value=False):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _register_windows_trigger("myws", "mytrigger", "0 6 * * *", "dataforge", "C:\\DataForge")

    assert mock_run.called
    call_args = mock_run.call_args[0][0]
    # Must be an argument list (not a string)
    assert isinstance(call_args, list), "subprocess.run must use argument list, not shell string"
    # PowerShell invocation
    assert "powershell.exe" in call_args
    # Task name in powershell script
    ps_script = mock_run.call_args[0][0][-1]
    assert "DataForge\\myws\\trigger\\mytrigger" in ps_script
    assert "scheduled-run" in ps_script
    # df-413: must tolerate battery power so missed runs can start
    assert "-AllowStartIfOnBatteries" in ps_script
    assert "-DontStopIfGoingOnBatteries" in ps_script


def test_register_windows_task_battery_flags():
    """_register_windows_task settings tolerate battery power (df-413)."""
    from dataforge.cli import _register_windows_task

    with patch("dataforge.cli.subprocess.run") as mock_run, \
         patch("dataforge.cli._is_wsl", return_value=False):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _register_windows_task("myws", "myjob", "0 6 * * *")

    ps_script = mock_run.call_args[0][0][-1]
    assert "-AllowStartIfOnBatteries" in ps_script
    assert "-DontStopIfGoingOnBatteries" in ps_script


def test_deregister_windows_trigger_splits_taskname_and_path():
    """Unregister must split leaf name from folder, else it silently no-ops (df-413)."""
    from dataforge.cli import _deregister_windows_trigger

    with patch("dataforge.cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _deregister_windows_trigger("myws", "mytrigger")

    ps_script = mock_run.call_args[0][0][-1]
    assert "-TaskName 'mytrigger'" in ps_script
    assert "-TaskPath '\\DataForge\\myws\\trigger\\'" in ps_script
    # the full path must NOT be jammed into -TaskName (the original bug)
    assert "-TaskName 'DataForge\\myws\\trigger\\mytrigger'" not in ps_script


# ---------------------------------------------------------------------------
# df-413: Task Scheduler history enablement (Windows)
# ---------------------------------------------------------------------------

def test_ensure_history_already_enabled_no_op():
    """Operational log already enabled → no enable attempt, no warning."""
    from dataforge.cli import _ensure_task_scheduler_history

    with patch("dataforge.cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="enabled: true", stderr="")
        warning = _ensure_task_scheduler_history()

    assert warning is None
    assert mock_run.call_count == 1  # only the query, no /e:true call


def test_ensure_history_enable_succeeds_no_warning():
    """Log disabled but enable succeeds (elevated) → no warning."""
    from dataforge.cli import _ensure_task_scheduler_history

    gl = MagicMock(returncode=0, stdout="enabled: false", stderr="")
    sl = MagicMock(returncode=0, stdout="", stderr="")
    with patch("dataforge.cli.subprocess.run", side_effect=[gl, sl]):
        warning = _ensure_task_scheduler_history()

    assert warning is None


def test_ensure_history_elevation_prompt_succeeds_no_warning():
    """Direct enable fails but the UAC elevation prompt is approved → no warning."""
    from dataforge.cli import _ensure_task_scheduler_history

    gl = MagicMock(returncode=0, stdout="enabled: false", stderr="")
    sl = MagicMock(returncode=1, stdout="", stderr="Access is denied.")
    elevated = MagicMock(returncode=0, stdout="", stderr="")
    with patch("dataforge.cli.subprocess.run", side_effect=[gl, sl, elevated]) as mock_run:
        warning = _ensure_task_scheduler_history()

    assert warning is None
    # third call must be the elevated Start-Process -Verb RunAs attempt
    elevate_script = mock_run.call_args_list[2][0][0][-1]
    assert "Start-Process" in elevate_script
    assert "-Verb RunAs" in elevate_script


def test_ensure_history_elevation_declined_returns_actionable_warning():
    """Log disabled, direct enable fails, and elevation is declined → warning."""
    from dataforge.cli import _ensure_task_scheduler_history

    gl = MagicMock(returncode=0, stdout="enabled: false", stderr="")
    sl = MagicMock(returncode=1, stdout="", stderr="Access is denied.")
    elevated = MagicMock(returncode=1, stdout="", stderr="")  # user cancelled UAC
    with patch("dataforge.cli.subprocess.run", side_effect=[gl, sl, elevated]):
        warning = _ensure_task_scheduler_history()

    assert warning is not None
    assert "wevtutil sl" in warning
    assert "Operational" in warning


def test_platform_register_trigger_returns_history_warning():
    """On Windows, _platform_register_trigger surfaces the history-enable warning."""
    from dataforge.cli import _platform_register_trigger

    with patch("dataforge.cli.platform.system", return_value="Windows"), \
         patch("dataforge.cli._is_wsl", return_value=False), \
         patch("dataforge.cli._register_windows_trigger"), \
         patch("dataforge.cli._ensure_task_scheduler_history", return_value="WARN-XYZ"):
        result = _platform_register_trigger("ws", "trig", "0 6 * * *")

    assert result == "WARN-XYZ"


def test_platform_register_trigger_linux_returns_none():
    """Non-Windows platforms produce no history warning."""
    from dataforge.cli import _platform_register_trigger

    with patch("dataforge.cli.platform.system", return_value="Linux"), \
         patch("dataforge.cli._is_wsl", return_value=False), \
         patch("dataforge.cli._register_crontab"):
        result = _platform_register_trigger("ws", "trig", "0 6 * * *")

    assert result is None


# ---------------------------------------------------------------------------
# SCHED-02 + SCHED-03: API → OS registration wiring tests (Task 3 — TDD RED)
# ---------------------------------------------------------------------------

def test_create_trigger_api_registers_os(test_client, workspace_dir):
    """POST /api/workspaces/{ws}/triggers calls _platform_register_trigger once."""
    payload = {
        "name": "wired_trigger",
        "cron": "0 6 * * *",
        "enabled": True,
        "jobs": [],
    }
    with patch("dataforge.api.routers.triggers._platform_register_trigger") as mock_reg, \
         patch("dataforge.api.routers.triggers._platform_deregister_trigger"):
        mock_reg.return_value = None
        response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)

    assert response.status_code == 201
    mock_reg.assert_called_once_with("test_ws", "wired_trigger", "0 6 * * *")


def test_create_trigger_api_includes_history_warning(test_client, workspace_dir):
    """Registration warning is surfaced in the response 'warnings' list."""
    payload = {"name": "warn_trigger", "cron": "0 6 * * *", "enabled": True, "jobs": []}
    with patch(
        "dataforge.api.routers.triggers._platform_register_trigger",
        return_value="HIST-WARN",
    ), patch("dataforge.api.routers.triggers._platform_deregister_trigger"):
        response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)

    assert response.status_code == 201
    assert "HIST-WARN" in response.json().get("warnings", [])


def test_create_trigger_api_no_warning_when_clean(test_client, workspace_dir):
    """No warning from registration → empty/absent warnings, response still valid."""
    payload = {"name": "clean_trigger", "cron": "0 6 * * *", "enabled": True, "jobs": []}
    with patch(
        "dataforge.api.routers.triggers._platform_register_trigger",
        return_value=None,
    ), patch("dataforge.api.routers.triggers._platform_deregister_trigger"):
        response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)

    assert response.status_code == 201
    assert response.json().get("warnings", []) == []


def test_delete_trigger_api_deregisters_os(test_client, workspace_dir):
    """DELETE /api/workspaces/{ws}/triggers/{name} calls _platform_deregister_trigger once."""
    write_trigger_yaml(
        workspace_dir,
        "del_trigger",
        "name: del_trigger\ncron: '0 5 * * *'\nenabled: true\njobs: []\n",
    )
    with patch("dataforge.api.routers.triggers._platform_deregister_trigger") as mock_dereg:
        response = test_client.delete("/api/workspaces/test_ws/triggers/del_trigger")

    assert response.status_code == 204
    mock_dereg.assert_called_once_with("test_ws", "del_trigger")


def test_register_os_trigger_imports_cli(workspace_dir):
    """_register_os_trigger body calls _platform_register_trigger (not a no-op)."""
    from dataforge.api.routers.triggers import _register_os_trigger
    from dataforge.models import TriggerModel

    trigger = TriggerModel(name="check", cron="0 6 * * *", enabled=True, jobs=[])
    with patch("dataforge.api.routers.triggers._platform_register_trigger") as mock_reg:
        _register_os_trigger("ws1", trigger)
    mock_reg.assert_called_once_with("ws1", "check", "0 6 * * *")


# ---------------------------------------------------------------------------
# SCHED-03: Daemon startup scan tests (Plan 03, Task 1 — TDD RED)
# ---------------------------------------------------------------------------

def test_startup_scan_publishes_running(dataforge_home, global_engine):
    """Seeded running JobRunORM row → lifespan triggers get_broadcaster().publish with that run."""
    from sqlalchemy.orm import Session
    from dataforge.db import JobRunORM, utcnow

    # Seed a running row in the global DB before the app starts
    with Session(global_engine) as session:
        session.add(JobRunORM(
            id="run-scan-001",
            workspace="test_ws",
            job="test_job",
            status="running",
            started_at=utcnow(),
            owner_id="system",
        ))
        session.commit()

    published = []

    async def _fake_publish(event):
        published.append(event)

    mock_broadcaster = MagicMock()
    mock_broadcaster.publish = _fake_publish

    with patch("dataforge.api.broadcaster.get_broadcaster", return_value=mock_broadcaster) as mock_get:
        from dataforge.api import app
        from fastapi.testclient import TestClient
        with TestClient(app):
            pass  # lifespan runs on enter

    assert any(e.get("run_id") == "run-scan-001" for e in published), (
        f"Expected publish with run_id='run-scan-001', got: {published}"
    )
    assert any(e.get("status") == "running" for e in published)


def test_startup_scan_no_running_clean(dataforge_home, global_engine):
    """Empty global DB → app lifespan starts without exception, no publish calls."""
    published = []

    async def _fake_publish(event):
        published.append(event)

    mock_broadcaster = MagicMock()
    mock_broadcaster.publish = _fake_publish

    with patch("dataforge.api.broadcaster.get_broadcaster", return_value=mock_broadcaster):
        from dataforge.api import app
        from fastapi.testclient import TestClient
        with TestClient(app):
            pass  # lifespan runs on enter

    assert published == [], f"Expected no publish calls for empty DB, got: {published}"


# ---------------------------------------------------------------------------
# SCHED-03: /notify endpoint tests (Plan 03, Task 2 — TDD RED)
# ---------------------------------------------------------------------------

def test_notify_endpoint_ok(test_client):
    """POST /api/triggers/notify → 2xx with JSON ack {"ok": true}."""
    response = test_client.post("/api/triggers/notify")
    assert response.status_code in (200, 201, 202, 204), (
        f"Expected 2xx, got {response.status_code}: {response.text}"
    )
    if response.status_code != 204:
        body = response.json()
        assert body.get("ok") is True, f"Expected ok=true, got: {body}"


def test_notify_endpoint_idempotent(test_client):
    """Two sequential POSTs to /api/triggers/notify both return 2xx."""
    r1 = test_client.post("/api/triggers/notify")
    r2 = test_client.post("/api/triggers/notify")
    assert r1.status_code in (200, 201, 202, 204), f"First POST: {r1.status_code}"
    assert r2.status_code in (200, 201, 202, 204), f"Second POST: {r2.status_code}"


# ---------------------------------------------------------------------------
# SCHED-04: Cron helper unit tests (Plan 05, Task 1 — TDD RED)
# ---------------------------------------------------------------------------

from datetime import datetime as _dt


def test_cron_validation():
    """cron_is_valid returns True for valid cron, False for invalid."""
    from dataforge.gui.panels.scheduler import cron_is_valid
    assert cron_is_valid("0 6 * * *") is True
    assert cron_is_valid("*/15 * * * *") is True
    assert cron_is_valid("not cron") is False
    assert cron_is_valid("99 99 99 99 99") is False


def test_next_run_computation():
    """next_run returns a datetime strictly greater than base for valid cron."""
    from dataforge.gui.panels.scheduler import next_run
    base = _dt(2026, 1, 1, 5, 0, 0)
    result = next_run("0 6 * * *", base)
    assert result is not None
    assert isinstance(result, _dt)
    # next run of "0 6 * * *" after 05:00 Jan 1 should be 06:00 Jan 1
    assert result > base
    assert result.hour == 6
    assert result.minute == 0


def test_next_run_invalid_cron():
    """next_run returns None for invalid cron (T-01.5-17: no crash)."""
    from dataforge.gui.panels.scheduler import next_run
    result = next_run("not valid cron")
    assert result is None


def test_preset_to_cron_daily():
    """preset 'daily' + time 06:00 → cron '0 6 * * *' and description 'Every day at 06:00'."""
    from dataforge.gui.panels.scheduler import preset_to_cron
    cron_str, description = preset_to_cron("daily", 6, 0)
    assert cron_str == "0 6 * * *"
    assert "06:00" in description or "6:00" in description
    assert "day" in description.lower()


def test_preset_to_cron_weekly():
    """preset 'weekly' + Monday + 08:30 → '30 8 * * 1' and description containing 'Monday'."""
    from dataforge.gui.panels.scheduler import preset_to_cron
    cron_str, description = preset_to_cron("weekly", 8, 30, weekday=1)
    assert cron_str == "30 8 * * 1"
    assert "Monday" in description
    assert "08:30" in description


def test_preset_to_cron_hourly():
    """preset 'hourly' → '0 * * * *' and description 'Every hour'."""
    from dataforge.gui.panels.scheduler import preset_to_cron
    cron_str, description = preset_to_cron("hourly", 0, 0)
    assert cron_str == "0 * * * *"
    assert "hour" in description.lower()


def test_preset_to_cron_monthly():
    """preset 'monthly' + day 15 + 09:00 → '0 9 15 * *' and description with 'day 15'."""
    from dataforge.gui.panels.scheduler import preset_to_cron
    cron_str, description = preset_to_cron("monthly", 9, 0, dom=15)
    assert cron_str == "0 9 15 * *"
    assert "15" in description


def test_advanced_mode_custom():
    """Advanced/raw cron entry → description 'Custom schedule', next_run still computed via croniter."""
    from dataforge.gui.panels.scheduler import next_run, preset_to_cron
    # Advanced mode: any raw cron string yields "Custom schedule" description
    cron_str, description = preset_to_cron("advanced", 0, 0)
    assert description == "Custom schedule"
    # next_run still works for valid advanced cron
    base = _dt(2026, 1, 1, 5, 0, 0)
    result = next_run("30 8 * * 1", base)
    assert result is not None
    assert result > base


# ---------------------------------------------------------------------------
# Crons croniter accepts but the OS scheduler cannot express (df-vo3x.3): refused with a 400
# before anything is saved or deregistered.
# ---------------------------------------------------------------------------


_PLATFORMS = {
    "windows": ("Windows", False),
    "wsl": ("Linux", True),
    "macos": ("Darwin", False),
    "linux": ("Linux", False),
}


@pytest.fixture
def on_platform(monkeypatch):
    def _set(key):
        system, wsl = _PLATFORMS[key]
        monkeypatch.setattr("dataforge.cli.platform.system", lambda: system)
        monkeypatch.setattr("dataforge.cli._is_wsl", lambda: wsl)
    return _set


# cron stored in UTC; '0 12 ...' stays on the same local day for any offset within ±11h
_REJECTED = [
    ("windows", "0 12 1 * *"),        # monthly: Task Scheduler gets daily/weekly only
    ("wsl", "*/15 * * * *"),          # minute step
    ("macos", "*/15 * * * *"),        # step: no single StartCalendarInterval
    ("macos", "0 12 * * 1-5"),        # range
    ("linux", "0 12 * * * 0"),        # 6 fields: crontab would read the 6th as the command
    ("linux", "@daily"),              # not shifted from UTC: would fire at local midnight
    ("windows", "@daily"),
]
_ACCEPTED = [
    ("windows", "0 12 * * *"), ("windows", "0 12 * * 1"), ("wsl", "30 12 * * *"),
    ("macos", "0 12 1 * *"), ("macos", "5 * * * *"),
    ("linux", "*/15 * * * *"), ("linux", "0 12 * * 1-5"),
]


@pytest.mark.parametrize(("platform_key", "cron"), _REJECTED)
def test_platform_check_cron_rejects_what_the_os_scheduler_cannot_run(on_platform, platform_key,
                                                                      cron):
    from dataforge.cli import _platform_check_cron
    on_platform(platform_key)
    with pytest.raises(ValueError):
        _platform_check_cron(cron)


@pytest.mark.parametrize(("platform_key", "cron"), _ACCEPTED)
def test_platform_check_cron_accepts_what_the_os_scheduler_runs(on_platform, platform_key, cron):
    from dataforge.cli import _platform_check_cron
    on_platform(platform_key)
    _platform_check_cron(cron)


@pytest.mark.parametrize(("platform_key", "cron"), _REJECTED)
def test_create_trigger_refuses_an_os_unsupported_cron_before_saving(
        test_client, workspace_dir, on_platform, platform_key, cron):
    from dataforge.api.routers import triggers
    on_platform(platform_key)
    payload = {"name": "monthly", "cron": cron, "enabled": True, "jobs": []}
    response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)
    assert response.status_code == 400
    assert cron in response.json()["detail"]
    assert not (workspace_dir / "triggers" / "monthly.yaml").exists()
    triggers._platform_register_trigger.assert_not_called()


@pytest.mark.parametrize(("platform_key", "cron"), _ACCEPTED)
def test_create_trigger_takes_an_os_supported_cron(test_client, workspace_dir, on_platform,
                                                   platform_key, cron):
    on_platform(platform_key)
    payload = {"name": "ok", "cron": cron, "enabled": True, "jobs": []}
    response = test_client.post("/api/workspaces/test_ws/triggers", json=payload)
    assert response.status_code == 201
    assert (workspace_dir / "triggers" / "ok.yaml").exists()


def test_update_trigger_refuses_an_os_unsupported_cron_and_keeps_the_old_one(
        test_client, workspace_dir, on_platform):
    from dataforge.api.routers import triggers
    on_platform("windows")
    old = "name: daily\ncron: '0 6 * * *'\nenabled: true\njobs: []\n"
    write_trigger_yaml(workspace_dir, "daily", old)
    payload = {"name": "daily", "cron": "0 12 1 * *", "enabled": True, "jobs": []}
    response = test_client.put("/api/workspaces/test_ws/triggers/daily", json=payload)
    assert response.status_code == 400
    assert (workspace_dir / "triggers" / "daily.yaml").read_text() == old
    triggers._platform_deregister_trigger.assert_not_called()
    triggers._platform_register_trigger.assert_not_called()


def test_reconcile_of_an_os_unsupported_cron_removes_the_stale_task_and_warns(
        test_client, workspace_dir, on_platform):
    from dataforge.api.routers import triggers
    on_platform("windows")
    write_trigger_yaml(workspace_dir, "monthly",
                       "name: monthly\ncron: '0 12 1 * *'\nenabled: true\njobs: []\n")
    response = test_client.post("/api/workspaces/test_ws/triggers/monthly/reconcile")
    assert response.status_code == 200
    assert "0 12 1 * *" in response.json()["warnings"][0]
    triggers._platform_register_trigger.assert_not_called()
    triggers._platform_deregister_trigger.assert_called_once_with("test_ws", "monthly")


def test_utc_cron_to_local_moves_a_day_list_across_midnight(monkeypatch):
    """Registration shifts the stored UTC cron to local time, days included (df-vo3x.5)."""
    from datetime import datetime, timedelta, timezone
    import dataforge.cli as cli

    class _BRT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-3)))

    monkeypatch.setattr(cli, "datetime", _BRT)
    assert cli._utc_cron_to_local("0 1 * * 2,4") == "0 22 * * 1,3"
    assert cli._utc_cron_to_local("0 1 * * TUE-SAT") == "0 22 * * 1,2,3,4,5"
