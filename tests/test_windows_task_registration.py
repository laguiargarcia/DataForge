"""How DataForge registers its tasks with the OS scheduler (df-18s6, df-muzy, df-3ul).

Windows: every task gets the S4U logon (runs whether or not the user is logged on, no password).
When Windows refuses S4U without elevation, the UAC prompt is tried once, then the task is still
registered as interactive and a warning says how to fix it. From WSL the task keeps the
interactive logon. The command is the DataForge that registers it, never the first one on PATH.
"""
import base64
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from dataforge import cli

OK = MagicMock(returncode=0, stdout="", stderr="")
DENIED = MagicMock(returncode=1, stdout="", stderr=(
    "Register-ScheduledTask : Access is denied.\n"
    "    + FullyQualifiedErrorId : HRESULT 0x80070005,Register-ScheduledTask"))
OTHER_ERROR = MagicMock(returncode=1, stdout="", stderr="The task XML is malformed. (0x80041318)")


def _scripts(mock_run):
    """The PowerShell script of each subprocess.run call."""
    return [c[0][0][-1] for c in mock_run.call_args_list]


def _register(side_effect, wsl=False, bin_="C:\\venv\\Scripts\\dataforge.exe"):
    with patch("dataforge.cli.subprocess.run", side_effect=side_effect) as run, \
         patch("dataforge.cli._is_wsl", return_value=wsl):
        warning = cli._register_windows_trigger("myws", "daily", "0 6 * * *", bin_,
                                                "C:\\DataForge")
    return warning, run


# ── logon: S4U ───────────────────────────────────────────────────────────────

def test_a_trigger_is_registered_to_run_whether_or_not_the_user_is_logged_on():
    warning, run = _register([OK])
    assert warning is None
    assert run.call_count == 1
    script = _scripts(run)[0]
    assert "New-ScheduledTaskPrincipal -UserId " \
           "([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) " \
           "-LogonType S4U -RunLevel Limited" in script
    assert "-Principal $principal" in script
    assert "-Password" not in script
    assert "Get-Credential" not in script


def test_access_denied_asks_for_elevation_and_registers_the_same_s4u_script():
    warning, run = _register([DENIED, OK])
    assert warning is None
    first, launcher = _scripts(run)
    assert "-Verb RunAs" in launcher
    encoded = launcher.split("'-EncodedCommand','")[1].split("'")[0]
    assert base64.b64decode(encoded).decode("utf-16-le") == first


def test_declined_elevation_still_registers_the_task_as_interactive_with_a_warning():
    warning, run = _register([DENIED, MagicMock(returncode=1, stdout="", stderr=""), OK])
    assert run.call_count == 3
    fallback = _scripts(run)[2]
    assert "-LogonType Interactive" in fallback
    assert "-LogonType S4U" not in fallback
    assert "Start-Process" not in fallback
    assert "only while you are logged on" in warning
    assert "elevated terminal" in warning
    assert "C:\\venv\\Scripts\\dataforge.exe register-triggers myws --trigger daily" in warning


def test_another_registration_error_is_raised_without_elevation_or_fallback():
    with pytest.raises(RuntimeError, match="malformed"):
        _register([OTHER_ERROR])


def test_a_failing_fallback_is_raised():
    with pytest.raises(RuntimeError, match="registration failed"):
        _register([DENIED, MagicMock(returncode=1, stdout="", stderr=""), OTHER_ERROR])


def test_from_wsl_the_task_keeps_the_interactive_logon_and_says_so():
    warning, run = _register([OK], wsl=True, bin_="/home/u/venv/bin/dataforge")
    assert run.call_count == 1
    script = _scripts(run)[0]
    assert "-LogonType Interactive" in script
    assert "S4U" not in script
    assert "from WSL" in warning
    assert "logged on" in warning


def test_a_failing_wsl_registration_is_raised():
    with pytest.raises(RuntimeError, match="registration failed"):
        _register([OTHER_ERROR], wsl=True)


# ── settings ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wsl", [False, True])
def test_settings_keep_the_df413_hardening_and_add_nothing_that_can_skip_a_run(wsl):
    _, run = _register([OK], wsl=wsl)
    settings = next(ln for ln in _scripts(run)[0].splitlines() if ln.startswith("$settings"))
    assert settings == ("$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
                        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries")
    # RunOnlyIfNetworkAvailable can leave a day without a run; Task Scheduler's RestartOnFailure
    # does not fire on a non-zero exit; WakeToRun changes the machine's power behaviour (df-muzy).


def test_a_schedule_sync_job_task_gets_the_same_logon_and_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    with patch("dataforge.cli.subprocess.run", side_effect=[OK]) as run, \
         patch("dataforge.cli._is_wsl", return_value=False):
        assert cli._register_windows_task("myws", "myjob", "0 6 * * *") is None
    script = _scripts(run)[0]
    assert "-LogonType S4U" in script
    assert "-TaskName 'DataForge\\myws\\myjob'" in script
    assert " run myws myjob" in script


def test_a_schedule_sync_fallback_warning_points_at_schedule_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    with patch("dataforge.cli.subprocess.run",
               side_effect=[DENIED, MagicMock(returncode=1, stderr=""), OK]), \
         patch("dataforge.cli._is_wsl", return_value=False):
        warning = cli._register_windows_task("myws", "myjob", "0 6 * * *")
    assert warning.rstrip().endswith("schedule-sync")


# ── the action: which command, quoted how ────────────────────────────────────

def test_the_windows_action_quotes_paths_with_spaces_and_quotes():
    _, run = _register([OK], bin_="C:\\Program Files\\it's\\dataforge.exe")
    action = _scripts(run)[0].splitlines()[0]
    # Inside the -Argument literal every ' is doubled once more.
    assert ("-Command \"$env:DATAFORGE_HOME=''C:\\DataForge''; "
            "& ''C:\\Program Files\\it''''s\\dataforge.exe'' scheduled-run myws daily\"") in action
    assert action.startswith("$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
                             "-Argument '-NoProfile -NonInteractive -Command ")


def test_the_windows_action_runs_the_interpreter_fallback_as_separate_tokens():
    _, run = _register([OK], bin_=["C:\\py\\python.exe", "-m", "dataforge.cli"])
    action = _scripts(run)[0].splitlines()[0]
    assert "& ''C:\\py\\python.exe'' ''-m'' ''dataforge.cli'' scheduled-run myws daily" in action


def test_the_wsl_action_splits_the_interpreter_fallback_into_bash_tokens():
    """df-3ul: 'python -m dataforge' was quoted as ONE bash token, so the task never ran."""
    _, run = _register([OK], wsl=True, bin_=["/mnt/c/x y/python", "-m", "dataforge.cli"])
    action = _scripts(run)[0].splitlines()[0]
    assert ("-e bash -c \"DATAFORGE_HOME=''C:\\DataForge'' ''/mnt/c/x y/python'' -m dataforge.cli "
            "scheduled-run myws daily\"") in action


def test_the_command_is_the_console_script_of_the_running_interpreter(tmp_path):
    script = tmp_path / ("dataforge.exe" if os.name == "nt" else "dataforge")
    script.write_bytes(b"")
    with patch("dataforge.cli.sysconfig.get_path", return_value=str(tmp_path)), \
         patch("shutil.which", return_value="/somewhere/else/dataforge"):
        assert cli._dataforge_command() == [str(script)]


def test_without_a_console_script_the_command_is_this_interpreter_with_the_cli_module(tmp_path):
    with patch("dataforge.cli.sysconfig.get_path", return_value=str(tmp_path)), \
         patch("shutil.which", return_value="/somewhere/else/dataforge"):
        assert cli._dataforge_command() == [sys.executable, "-m", "dataforge.cli"]


def test_the_cli_module_fallback_really_starts_dataforge():
    import subprocess
    proc = subprocess.run([sys.executable, "-m", "dataforge.cli", "--help"],
                          capture_output=True, text=True, check=False,
                          cwd=Path(cli.__file__).parent.parent)
    assert proc.returncode == 0, proc.stderr
    assert "scheduled-run" in proc.stdout


# ── Linux and macOS keep working with the token command ──────────────────────

def _platform_register(system, command, tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path / "home dir"))
    with patch("dataforge.cli.platform.system", return_value=system), \
         patch("dataforge.cli._is_wsl", return_value=False), \
         patch("dataforge.cli._dataforge_command", return_value=command), \
         patch("dataforge.cli._utc_cron_to_local", side_effect=lambda c: c), \
         patch("dataforge.cli.Path.home", return_value=tmp_path), \
         patch("dataforge.cli.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert cli._platform_register_trigger("myws", "daily", "0 6 * * *") is None
    return run


def test_linux_crontab_line_quotes_each_token(tmp_path, monkeypatch):
    run = _platform_register("Linux", ["/opt/my venv/bin/python", "-m", "dataforge.cli"],
                             tmp_path, monkeypatch)
    line = run.call_args_list[1][1]["input"].splitlines()[-1]
    assert line == (f"0 6 * * * DATAFORGE_HOME='{tmp_path / 'home dir'}' "
                    "'/opt/my venv/bin/python' -m dataforge.cli scheduled-run myws daily  "
                    "# DataForge:myws:daily")


def test_macos_plist_gets_one_program_argument_per_token(tmp_path, monkeypatch):
    _platform_register("Darwin", ["/opt/py", "-m", "dataforge.cli"], tmp_path, monkeypatch)
    plist = (tmp_path / "Library" / "LaunchAgents" / "com.dataforge.myws.daily.plist").read_text(
        encoding="utf-8")
    args = plist.split("<key>ProgramArguments</key>")[1].split("</array>")[0]
    strings = [ln.strip() for ln in args.splitlines() if "<string>" in ln]
    assert strings == ["<string>/opt/py</string>", "<string>-m</string>",
                       "<string>dataforge.cli</string>", "<string>scheduled-run</string>",
                       "<string>myws</string>", "<string>daily</string>"]


def test_windows_dispatch_joins_the_registration_and_history_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    with patch("dataforge.cli.platform.system", return_value="Windows"), \
         patch("dataforge.cli._is_wsl", return_value=False), \
         patch("dataforge.cli._register_windows_trigger", return_value="LOGON") as reg, \
         patch("dataforge.cli._ensure_task_scheduler_history", return_value="HISTORY"):
        assert cli._platform_register_trigger("ws", "t", "0 6 * * *") == "LOGON\n\nHISTORY"
    assert reg.call_args[0][3] == cli._dataforge_command()


# ── dataforge register-triggers ──────────────────────────────────────────────

def _write_trigger(ws_dir, name, enabled=True, cron="0 9 * * *"):
    (ws_dir / "triggers").mkdir(exist_ok=True)
    (ws_dir / "triggers" / f"{name}.yaml").write_bytes(
        f"name: {name}\ncron: '{cron}'\nenabled: {str(enabled).lower()}\njobs: []\n".encode())


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws_dir = tmp_path / "workspaces" / "myws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_bytes(b"name: myws\n")
    _write_trigger(ws_dir, "daily")
    _write_trigger(ws_dir, "old", enabled=False)
    return ws_dir


def _invoke(*args, register=None):
    register = register or MagicMock(return_value=None)
    with patch("dataforge.cli._platform_register_trigger", register), \
         patch("dataforge.cli._platform_deregister_trigger") as dereg, \
         patch("dataforge.cli._platform_check_cron"):
        result = CliRunner().invoke(cli.app, ["register-triggers", *args])
    return result, register, dereg


def test_register_triggers_registers_enabled_and_removes_disabled(ws):
    result, reg, dereg = _invoke("myws")
    assert result.exit_code == 0, result.output
    reg.assert_called_once_with("myws", "daily", "0 9 * * *")
    dereg.assert_called_once_with("myws", "old")


def test_register_triggers_only_the_named_trigger(ws):
    result, reg, dereg = _invoke("myws", "--trigger", "daily")
    assert result.exit_code == 0, result.output
    reg.assert_called_once_with("myws", "daily", "0 9 * * *")
    dereg.assert_not_called()


def test_register_triggers_prints_the_warning(ws):
    result, _, _ = _invoke("myws", "-t", "daily",
                           register=MagicMock(return_value="runs only while you are logged on"))
    assert result.exit_code == 0
    assert "runs only while you are logged on" in result.output


def test_register_triggers_exits_1_when_a_registration_fails(ws):
    result, _, _ = _invoke("myws", register=MagicMock(side_effect=RuntimeError("boom")))
    assert result.exit_code == 1
    assert "boom" in result.output


def test_register_triggers_refuses_an_unknown_trigger_before_touching_anything(ws):
    result, reg, dereg = _invoke("myws", "--trigger", "nope")
    assert result.exit_code == 1
    assert "nope" in result.output
    reg.assert_not_called()
    dereg.assert_not_called()


def test_register_triggers_refuses_an_unknown_workspace(ws):
    result, reg, _ = _invoke("missing")
    assert result.exit_code == 1
    reg.assert_not_called()


# ── the real Task Scheduler (CI windows runner only) ─────────────────────────

@pytest.mark.skipif(sys.platform != "win32" or not os.environ.get("GITHUB_ACTIONS"),
                    reason="registers a real scheduled task: only on a throwaway CI runner")
def test_on_windows_the_registered_task_really_has_the_s4u_logon(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws = f"DataForgeCi{uuid.uuid4().hex[:8]}"
    try:
        warning = cli._register_windows_trigger(ws, "daily", "0 6 * * *",
                                                cli._dataforge_command(), str(tmp_path))
        xml = subprocess.run(["schtasks.exe", "/Query", "/TN", f"\\DataForge\\{ws}\\trigger\\daily",
                              "/XML"], capture_output=True, text=True, check=True).stdout
    finally:
        cli._deregister_windows_trigger(ws, "daily")
    assert warning is None
    assert "<LogonType>S4U</LogonType>" in xml
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "scheduled-run " + ws + " daily" in xml
    gone = subprocess.run(["schtasks.exe", "/Query", "/TN", f"\\DataForge\\{ws}\\trigger\\daily"],
                          capture_output=True, text=True, check=False)
    assert gone.returncode != 0
