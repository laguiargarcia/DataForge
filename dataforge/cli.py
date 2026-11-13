import base64
import os
import platform
import shlex
import subprocess
import sys
import sysconfig
import time
import httpx
import typer
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from typing import Annotated, Optional
from pydantic import ValidationError
from rich.console import Console
from rich import print as rprint
from rich.markup import escape as rich_escape
from dataforge import instance
from dataforge.apps import discover_apps
from dataforge.compat import (
    ALLOWED_HOSTS_ENV, DEFAULT_API_PORT, PORT_ENV, api_port, set_api_port, resolve_workspace, resolve_db, data_dir, db_url,
    list_workspaces, set_data_dir, valid_name,
)
from dataforge.cron import CRON_FIELDS, shift_cron
from dataforge.dag import topological_levels
from dataforge.deploy.cli import deploy_app
from dataforge.parser import load_connections, load_jobs, load_triggers, load_workspace

app = typer.Typer(help="DataForge — workspace local de engenharia de dados")
jobs_app = typer.Typer(help="Gerenciar jobs")
execucoes_app = typer.Typer(help="Ver histórico de execuções")
app.add_typer(jobs_app, name="jobs")
app.add_typer(execucoes_app, name="execucoes")
app.add_typer(deploy_app, name="deploy")

console = Console()

_WORKSPACE_HELP = "Nome do workspace (em workspaces/)"
_DB_FILE = "dataforge.db"
_POWERSHELL = "powershell.exe"


def _field_errors(where: str, e: ValidationError) -> list[str]:
    return [f"{where}: campo '{'.'.join(str(x) for x in err['loc'])}' — {err['msg']}" for err in e.errors()]


def _check_workspace_yaml(workspace_path: Path) -> list[str]:
    try:
        ws = load_workspace(workspace_path)
    except ValidationError as e:
        return _field_errors("workspace.yaml", e)
    except FileNotFoundError:
        return ["workspace.yaml não encontrado"]
    rprint(f"[green]✓[/green] workspace.yaml — {ws.name} (engine={ws.engine.value})")
    return []


def _check_jobs(workspace_path: Path) -> tuple[list, list[str]]:
    """The jobs that loaded (none if any job failed to load), and the load errors."""
    try:
        jobs = load_jobs(workspace_path)
    except ValidationError as e:
        return [], _field_errors("jobs/", e)
    except Exception as e:
        return [], [f"jobs/: {e}"]
    rprint(f"[green]✓[/green] jobs/ — {len(jobs)} job(s) carregados")
    for job in jobs:
        rprint(f"  [cyan]{job.name}[/cyan] — {len(job.tasks)} task(s)")
    return jobs, []


def _check_connections(workspace_path: Path) -> list[str]:
    try:
        conns = load_connections(workspace_path)
    except Exception as e:
        return [f"connections.yaml: {e}"]
    rprint(f"[green]✓[/green] connections.yaml — {len(conns)} conexão(ões)")
    return []


def _check_job_graphs(jobs: list) -> list[str]:
    """Per job: its task graph has no cycle, and every task.job names a job that exists."""
    errors = []
    job_names = {j.name for j in jobs}
    for job in jobs:
        try:
            topological_levels(job.tasks)
        except ValueError as e:
            errors.append(f"job '{job.name}': {e}")
        errors += [f"job '{job.name}', task '{task.name}': job referenciado '{task.job}' não existe"
                   for task in job.tasks if task.job and task.job not in job_names]
    return errors


@app.command()
def validate(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help=_WORKSPACE_HELP),
):
    """Valida todos os YAMLs do workspace."""
    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)

    errors = _check_workspace_yaml(workspace_path)
    jobs, job_errors = _check_jobs(workspace_path)
    errors += job_errors
    errors += _check_connections(workspace_path)
    errors += _check_job_graphs(jobs)

    if errors:
        rprint("\n[red]Erros encontrados:[/red]")
        for err in errors:
            rprint(f"  [red]✗[/red] {err}")
        raise typer.Exit(1)

    rprint("\n[green]Workspace válido.[/green]")


@app.command()
def init(
    name: str = typer.Argument(..., help="Nome do novo workspace"),
):
    """Cria estrutura de um novo workspace."""
    ws_dir = data_dir() / "workspaces" / name
    if ws_dir.exists():
        rprint(f"[red]Erro:[/red] diretório '{name}' já existe.")
        raise typer.Exit(1)

    (ws_dir / "connections").mkdir(parents=True)
    (ws_dir / "jobs").mkdir()
    (ws_dir / "scripts").mkdir()
    (ws_dir / "logs").mkdir()

    (ws_dir / "workspace.yaml").write_text(
        f'name: {name}\nversion: "1.0"\nengine: duckdb\ndatabase: sqlite\n',
        encoding="utf-8",
    )
    (ws_dir / "connections" / "connections.yaml").write_text(
        "connections:\n  raw_data:\n    type: parquet\n    path: ./data/raw\n",
        encoding="utf-8",
    )

    rprint(f"[green]✓[/green] Workspace '{name}' criado em {ws_dir}/")
    rprint("  Próximo passo: adicione jobs em jobs/ e scripts em scripts/")


_DOW_NAMES = {
    "0": "Sunday", "7": "Sunday",
    "1": "Monday", "2": "Tuesday", "3": "Wednesday",
    "4": "Thursday", "5": "Friday", "6": "Saturday",
}

_DOW_ORDER = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _expand_dow(field: str) -> list[str]:
    days: set[str] = set()
    for part in field.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            for i in range(int(a), int(b) + 1):
                name = _DOW_NAMES.get(str(i))
                if name is None:
                    raise ValueError(f"Invalid day-of-week value: {i}")
                days.add(name)
        else:
            name = _DOW_NAMES.get(part.strip())
            if name is None:
                raise ValueError(f"Invalid day-of-week value: {part.strip()!r}")
            days.add(name)
    return [d for d in _DOW_ORDER if d in days]


def _cron_to_ps_trigger(cron: str) -> str:
    """Translate a cron expression to a New-ScheduledTaskTrigger argument string."""
    parts = cron.strip().split()
    if len(parts) != CRON_FIELDS:
        raise ValueError(f"Invalid cron expression (need 5 fields): {cron!r}")
    minute, hour, dom, month, dow = parts
    if not minute.isdigit() or not hour.isdigit():
        raise ValueError(
            f"Unsupported cron expression: {cron!r}. "
            "Minute and hour must be plain integers."
        )
    at_time = f"{int(hour):02d}:{int(minute):02d}"
    if dom == "*" and month == "*" and dow == "*":
        return f'-Daily -At "{at_time}"'
    if dom == "*" and month == "*":
        days = _expand_dow(dow)
        return f'-Weekly -DaysOfWeek {",".join(days)} -At "{at_time}"'
    raise ValueError(
        f"Unsupported cron expression: {cron!r}. "
        "Only daily ('M H * * *') and weekly ('M H * * DOW') patterns are supported."
    )


def _upsert_and_get_enabled(workspace: str, job_name: str, cron: str) -> bool:
    from dataforge.db import init_db, ScheduleORM
    from sqlalchemy.orm import Session

    global_db_url = db_url(data_dir() / _DB_FILE)
    engine = init_db(global_db_url)
    row_id = f"{workspace}:{job_name}"
    with Session(engine) as session:
        row = session.get(ScheduleORM, row_id)
        if row is None:
            row = ScheduleORM(id=row_id, workspace=workspace, job=job_name, cron=cron)
            session.add(row)
        else:
            row.cron = cron
        session.commit()
        session.refresh(row)
        return row.enabled


def _check_scheduler_names(**names: str) -> None:
    """Raise ValueError unless every name is a valid_name. Called by every function that puts a
    workspace, trigger or job name into an OS scheduler command: whatever the caller (API, deploy
    reconcile, CLI), an unsafe name never reaches PowerShell, bash, crontab or a plist (df-utq8)."""
    for kind, value in names.items():
        if not valid_name(value):
            raise ValueError(f"invalid {kind} name for the OS scheduler: {value!r} "
                             "(only letters, digits, '-' and '_')")


def _is_wsl() -> bool:
    """True when running inside Windows Subsystem for Linux."""
    return "microsoft" in platform.release().lower()


def _register_crontab(workspace: str, trigger_name: str, cron: str, cmd: str) -> None:
    """Register a trigger in the user crontab (Linux). Delete+recreate contract (D-05)."""
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    marker = f"# DataForge:{workspace}:{trigger_name}"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    # crontab -l exits non-zero on empty crontab — treat both as "no existing" (T-01.5-06)
    existing = result.stdout if result.returncode == 0 else ""
    lines = [ln for ln in existing.splitlines() if marker not in ln]
    lines.append(f"{cron} {cmd}  {marker}")
    new_crontab = "\n".join(lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)


def _deregister_crontab(workspace: str, trigger_name: str) -> None:
    """Remove a trigger entry from the user crontab."""
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    marker = f"# DataForge:{workspace}:{trigger_name}"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""
    lines = [ln for ln in existing.splitlines() if marker not in ln]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


# StartCalendarInterval key and accepted range for each cron field, in cron order.
_LAUNCHD_FIELDS = (("Minute", 0, 59), ("Hour", 0, 23), ("Day", 1, 31), ("Month", 1, 12),
                   ("Weekday", 0, 7))


def _cron_to_launchd_interval(cron: str) -> dict[str, int]:
    """Translate a cron expression to a launchd StartCalendarInterval dict.

    A '*' field is left out (launchd treats a missing key as "every"); a plain integer becomes
    its key. Steps, lists, ranges and names have no single-dict form, so they raise ValueError
    instead of being dropped, which would make the job fire more often than asked (df-vo3x.1).
    Day and Weekday may both be set: launchd starts the job when either matches, as cron does.
    """
    parts = cron.strip().split()
    if len(parts) != len(_LAUNCHD_FIELDS):
        raise ValueError(f"Invalid cron expression (need 5 fields): {cron!r}")
    cal: dict[str, int] = {}
    for value, (key, low, high) in zip(parts, _LAUNCHD_FIELDS, strict=True):
        if value == "*":
            continue
        if not value.isdigit() or not low <= int(value) <= high:
            raise ValueError(
                f"Unsupported cron expression for launchd: {cron!r}. "
                f"Each field must be '*' or a plain integer; {key} {value!r} is not "
                f"(allowed {low}-{high})."
            )
        cal[key] = int(value)
    return cal


def _register_launchd(
    workspace: str, trigger_name: str, cron: str, dataforge_bin: str | list[str], home: str
) -> None:
    """Register a trigger with macOS launchd. Delete+recreate contract (D-05)."""
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    cal = _cron_to_launchd_interval(cron)

    cal_items = "\n".join(
        f"                <key>{k}</key>\n                <integer>{v}</integer>"
        for k, v in cal.items()
    )
    program = "\n".join(f"        <string>{xml_escape(t)}</string>"
                         for t in _command_tokens(dataforge_bin))
    label = f"com.dataforge.{workspace}.{trigger_name}"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{xml_escape(label)}</string>
    <key>ProgramArguments</key>
    <array>
{program}
        <string>scheduled-run</string>
        <string>--</string>
        <string>{xml_escape(workspace)}</string>
        <string>{xml_escape(trigger_name)}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>DATAFORGE_HOME</key>
        <string>{home}</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
{cal_items}
    </dict>
</dict>
</plist>
"""
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content, encoding="utf-8")

    # Delete+recreate: unload (ignore failure) then load
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)


def _deregister_launchd(workspace: str, trigger_name: str) -> None:
    """Unload and remove a launchd plist for the given trigger."""
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    label = f"com.dataforge.{workspace}.{trigger_name}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    plist_path.unlink(missing_ok=True)


def _dataforge_command() -> list[str]:
    """The command an OS scheduler runs to start THIS DataForge installation.

    The console script installed next to the running interpreter (its venv's Scripts/ or bin/),
    or, without one, the interpreter itself with ``-m dataforge.cli``. Not ``shutil.which``: the
    first ``dataforge`` on PATH can belong to another venv, so re-registering from a shell with
    a different PATH would silently repoint the OS task at that venv (df-18s6). Returned as separate
    tokens so a shell never reads ``python -m dataforge.cli`` as one file name (df-3ul).
    """
    script = Path(sysconfig.get_path("scripts")) / ("dataforge.exe" if os.name == "nt" else "dataforge")
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "dataforge.cli"]


def _command_tokens(dataforge_bin: str | list[str]) -> list[str]:
    """A single executable path, or the token list of :func:`_dataforge_command`."""
    return [dataforge_bin] if isinstance(dataforge_bin, str) else list(dataforge_bin)


def _ps_literal(value: str) -> str:
    """A PowerShell single-quoted string literal: nothing inside it is expanded."""
    return "'" + value.replace("'", "''") + "'"


# Task Scheduler logon for every DataForge task (df-18s6). S4U runs the task as the registering
# user whether or not that user is logged on, without storing or asking for a password, in a
# non-interactive session (no console window a user can close: the 0xC000013A exit of
# 2026-11-11). It has no network credentials (SMB shares, mapped drives, Windows-integrated auth)
# and no DPAPI/EFS: a task that needs those must not run under it. Plain HTTPS and local files work.
_S4U = "S4U"
_INTERACTIVE = "Interactive"
# The HRESULT Register-ScheduledTask reports when the caller may not register the task as asked;
# an administrator under UAC gets it for S4U from a non-elevated process. Not localised.
_ACCESS_DENIED = "0x80070005"
_CURRENT_USER_PS = "([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
# Battery flags: df-413. No -RestartCount/-RestartInterval: Task Scheduler does not restart a task
# whose action exits non-zero (checked on Windows 11, df-muzy), so it would not retry a failed run.
_WINDOWS_TASK_SETTINGS = (
    "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
    "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries"
)


def _windows_action_line(dataforge_bin: str | list[str], home: str, args: list[str]) -> str:
    """The New-ScheduledTaskAction line that runs ``dataforge <args>`` with DATAFORGE_HOME=home:
    through wsl.exe + bash when DataForge lives in WSL, else through powershell.exe."""
    tokens = _command_tokens(dataforge_bin)
    if _is_wsl():
        wsl_cmd = " ".join([f"DATAFORGE_HOME={shlex.quote(str(home))}",
                            *(shlex.quote(t) for t in [*tokens, *args])])
        executable, argument = "wsl.exe", f'-e bash -c "{wsl_cmd}"'
    else:
        call = " ".join(["&", *(_ps_literal(t) for t in [*tokens, *args])])
        win_cmd = f"$env:DATAFORGE_HOME={_ps_literal(str(home))}; {call}"
        executable, argument = "powershell.exe", f'-NoProfile -NonInteractive -Command "{win_cmd}"'
    return (f"$action = New-ScheduledTaskAction -Execute '{executable}' "
            f"-Argument {_ps_literal(argument)}")


def _windows_register_script(task_name: str, action_line: str, trigger_args: str, logon: str) -> str:
    return "\n".join([
        action_line,
        f"$trigger = New-ScheduledTaskTrigger {trigger_args}",
        _WINDOWS_TASK_SETTINGS,
        f"$principal = New-ScheduledTaskPrincipal -UserId {_CURRENT_USER_PS} "
        f"-LogonType {logon} -RunLevel Limited",
        f"Register-ScheduledTask -TaskName {_ps_literal(task_name)} -Action $action "
        "-Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null",
    ])


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, check=False)


def _run_powershell_elevated(script: str) -> bool:
    """Run *script* in an elevated PowerShell after the UAC consent prompt (the same prompt
    _ensure_task_scheduler_history uses). False when declined, unavailable or failed."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    launcher = (
        "$ErrorActionPreference='Stop'; try { "
        "$p = Start-Process -FilePath 'powershell.exe' "
        f"-ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand','{encoded}') "
        "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
        "exit $p.ExitCode } catch { exit 1 }"
    )
    return _run_powershell(launcher).returncode == 0


def _register_windows_scheduled_task(
    task_name: str, action_line: str, trigger_args: str, rerun_hint: str
) -> str | None:
    """Register (or replace) a Task Scheduler task that runs whether or not the user is logged
    on (S4U, df-18s6). Returns a warning, or None when the task got the S4U logon.

    An administrator under UAC may not register S4U from a non-elevated process: then the UAC
    consent prompt is shown once; if it is declined or there is no desktop, the task is still
    registered, as 'only when the user is logged on', and the warning says how to fix it. Never
    a password. From WSL the task runs wsl.exe, which is not known to work in a non-interactive
    S4U session, so it keeps the interactive logon and says so.
    """
    if _is_wsl():
        result = _run_powershell(_windows_register_script(task_name, action_line, trigger_args,
                                                          _INTERACTIVE))
        if result.returncode != 0:
            raise RuntimeError(f"Task Scheduler registration failed:\n{result.stderr}")
        return (f"Task '{task_name}' was registered from WSL, so it runs only while you are "
                "logged on to Windows. Register it from DataForge installed on Windows to run it "
                "when you are logged off.")
    s4u = _windows_register_script(task_name, action_line, trigger_args, _S4U)
    result = _run_powershell(s4u)
    if result.returncode == 0:
        return None
    if _ACCESS_DENIED not in result.stderr:
        raise RuntimeError(f"Task Scheduler registration failed:\n{result.stderr}")
    if _run_powershell_elevated(s4u):
        return None
    fallback = _run_powershell(_windows_register_script(task_name, action_line, trigger_args,
                                                        _INTERACTIVE))
    if fallback.returncode != 0:
        raise RuntimeError(f"Task Scheduler registration failed:\n{fallback.stderr}")
    return (f"Task '{task_name}' runs only while you are logged on: Windows needs administrator "
            "permission to register it to run when you are logged off, and the elevation prompt "
            f"was declined or unavailable. To fix it, run in an elevated terminal:\n    {rerun_hint}")


def _register_windows_trigger(
    workspace: str, trigger_name: str, cron: str, dataforge_bin: str | list[str], home: str
) -> str | None:
    """Register a trigger with Windows Task Scheduler. Delete+recreate (D-05). Returns a warning
    (see _register_windows_scheduled_task) or None."""
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    trigger_args = _cron_to_ps_trigger(cron)
    task_name = f"DataForge\\{workspace}\\trigger\\{trigger_name}"
    action_line = _windows_action_line(dataforge_bin, home,
                                       ["scheduled-run", "--", workspace, trigger_name])
    rerun = " ".join([*_command_tokens(dataforge_bin), "register-triggers", workspace,
                      "--trigger", trigger_name])
    return _register_windows_scheduled_task(task_name, action_line, trigger_args, rerun)


def _deregister_windows_trigger(workspace: str, trigger_name: str) -> None:
    """Remove a Windows Task Scheduler task for the given trigger.

    Unregister-ScheduledTask does NOT split a path-qualified -TaskName the way
    Register-ScheduledTask does, so the folder must be passed separately as
    -TaskPath or the lookup silently misses and the task is orphaned (df-413).
    """
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    task_path = f"\\DataForge\\{workspace}\\trigger\\"
    ps_script = (
        f"Unregister-ScheduledTask -TaskName '{trigger_name}' -TaskPath '{task_path}' "
        f"-Confirm:$false -ErrorAction SilentlyContinue"
    )
    subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True,
    )


def _ensure_task_scheduler_history() -> str | None:
    """Enable the Windows TaskScheduler Operational log (df-413).

    Machine-global and admin-only, so best-effort and only ever runs when the
    log is actually disabled:

    1. If already enabled, no-op.
    2. Try a direct enable (succeeds silently when DataForge is already elevated).
    3. Otherwise ask the user for permission via a UAC elevation prompt
       (``Start-Process -Verb RunAs``). The prompt only appears here, never on
       subsequent trigger creates once the log is on.
    4. If the user declines or there is no interactive desktop, fall back to an
       actionable warning.
    """
    log = "Microsoft-Windows-TaskScheduler/Operational"
    enable_cmd = f'wevtutil sl "{log}" /e:true'
    elevate_ps = (
        "$ErrorActionPreference='Stop'; "
        "try { "
        f"$p = Start-Process -FilePath 'wevtutil.exe' "
        f"-ArgumentList @('sl','{log}','/e:true') "
        "-Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
        "exit $p.ExitCode } catch { exit 1 }"
    )
    try:
        query = subprocess.run(
            ["wevtutil.exe", "gl", log], capture_output=True, text=True
        )
        if query.returncode == 0 and "enabled: true" in query.stdout.lower():
            return None
        enabled = subprocess.run(
            ["wevtutil.exe", "sl", log, "/e:true"], capture_output=True, text=True
        )
        if enabled.returncode == 0:
            return None
        # Not already admin: ask the user for permission (UAC consent prompt).
        elevated = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", elevate_ps],
            capture_output=True, text=True,
        )
        if elevated.returncode == 0:
            return None
    except Exception:
        pass
    return (
        "Task Scheduler history is disabled and DataForge could not enable it "
        "(administrator permission was declined or unavailable). To record "
        "scheduled-run successes and failures, approve the elevation prompt next "
        f"time, or run this in an elevated terminal:\n    {enable_cmd}"
    )


def _utc_cron_to_local(cron: str) -> str:
    """Shift a cron from UTC to the current OS-local time (dataforge.cron.shift_cron).

    DataForge stores cron in UTC, but OS schedulers (schtasks, launchd, crond)
    interpret cron fields in local time, so we materialize the offset into the
    fields at registration time. Re-register after DST transitions to stay accurate.
    """
    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return cron
    return shift_cron(cron, offset)


def _platform_check_cron(cron: str) -> None:
    """Raise ValueError when this machine's OS scheduler cannot run *cron* (stored in UTC) as
    written, so a caller can refuse it before saving or deregistering anything (df-vo3x.3).

    Only a 5-field cron can be shifted from UTC to local time, and only what the platform's
    translator accepts can be registered: Task Scheduler takes daily and weekly, launchd one
    StartCalendarInterval, crontab any 5-field cron.
    """
    if len(cron.strip().split()) != CRON_FIELDS:
        raise ValueError(f"Unsupported cron expression: {cron!r}. "
                         "The OS scheduler needs the 5 fields 'M H DOM MON DOW'.")
    local = _utc_cron_to_local(cron)
    if platform.system() == "Windows" or _is_wsl():
        _cron_to_ps_trigger(local)
    elif platform.system() == "Darwin":
        _cron_to_launchd_interval(local)


def _platform_register_trigger(workspace: str, trigger_name: str, cron: str) -> str | None:
    """Dispatch to platform-specific OS trigger registration.

    Cron is stored in UTC; OS schedulers fire in local time, so shift at the boundary.
    Returns a non-fatal warning string (or None) for the caller to surface.
    """
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    command = _dataforge_command()
    home = str(data_dir())
    cron = _utc_cron_to_local(cron)

    if platform.system() == "Windows" or _is_wsl():
        warnings = [_register_windows_trigger(workspace, trigger_name, cron, command, home),
                    _ensure_task_scheduler_history()]
        return "\n\n".join(w for w in warnings if w) or None
    if platform.system() == "Darwin":
        _register_launchd(workspace, trigger_name, cron, command, home)
    else:  # Linux
        cmd = " ".join([f"DATAFORGE_HOME={shlex.quote(str(home))}",
                        *(shlex.quote(t) for t in [*command, "scheduled-run", "--", workspace,
                                                    trigger_name])])
        _register_crontab(workspace, trigger_name, cron, cmd)
    return None


def _platform_deregister_trigger(workspace: str, trigger_name: str) -> None:
    """Dispatch to platform-specific OS trigger deregistration."""
    _check_scheduler_names(workspace=workspace, trigger=trigger_name)
    if platform.system() == "Windows" or _is_wsl():
        _deregister_windows_trigger(workspace, trigger_name)
    elif platform.system() == "Darwin":
        _deregister_launchd(workspace, trigger_name)
    else:  # Linux
        _deregister_crontab(workspace, trigger_name)


def _register_windows_task(workspace: str, job_name: str, cron: str) -> str | None:
    """Register a job schedule (schedule-sync) with the same logon and settings as a trigger."""
    _check_scheduler_names(workspace=workspace, job=job_name)
    command = _dataforge_command()
    trigger_args = _cron_to_ps_trigger(cron)
    task_name = f"DataForge\\{workspace}\\{job_name}"
    action_line = _windows_action_line(command, str(data_dir()), ["run", "--", workspace, job_name])
    rerun = " ".join([*command, "schedule-sync"])
    return _register_windows_scheduled_task(task_name, action_line, trigger_args, rerun)


@app.command()
def gui() -> None:
    """Launch the DataForge desktop application."""
    import sys
    from dataforge.gui import run_app
    sys.exit(run_app())


@app.command("app")
def app_window_cmd(
    workspace: str = typer.Argument(..., help="Nome do workspace"),
    name: str = typer.Argument(..., help="Nome do app"),
    home: Optional[str] = typer.Option(None, "--home", help="Pasta de dados do DataForge (DATAFORGE_HOME)"),
    atalho: bool = typer.Option(
        False, "--atalho", help="Cria um atalho na Área de Trabalho (Windows) em vez de abrir a janela",
    ),
) -> None:
    """Abre um app do workspace em janela própria, sem navegador."""
    if home is not None:  # before anything resolves a path
        set_data_dir(home)
    if atalho:
        _app_shortcut(workspace, name)
        return
    from dataforge.gui.app_window import run_app_window  # noqa: PLC0415 -- Qt only when this command runs
    sys.exit(run_app_window(workspace, name))


def _app_shortcut(workspace: str, name: str) -> None:
    """``dataforge app --atalho``: validate the app, then create the Desktop shortcut."""
    from dataforge import shortcut  # noqa: PLC0415 -- only this command needs it
    from dataforge.apps import load_app  # noqa: PLC0415 -- only this command needs it
    console = Console()
    try:
        info = load_app(resolve_workspace(workspace), name)
    except ValueError as exc:
        console.print(f"[red]{rich_escape(str(exc))}[/red]", soft_wrap=True)
        raise typer.Exit(1) from exc
    if info is None:
        console.print(f"[red]App '{rich_escape(name)}' não encontrado no workspace '{rich_escape(workspace)}'.[/red]", soft_wrap=True)
        raise typer.Exit(1)
    home_dir = data_dir().resolve()  # a relative DATAFORGE_HOME would break the .lnk
    if sys.platform != "win32":
        args = shortcut.app_arguments(info, home_dir)
        console.print("O atalho só é criado no Windows. Para abrir o app, rode:", soft_wrap=True)
        console.print(rich_escape(" ".join(shlex.quote(a) for a in [sys.executable, *args])), soft_wrap=True)
        return
    try:
        lnk = shortcut.create_app_shortcut(info, home_dir)
    except RuntimeError as exc:
        console.print(f"[red]{rich_escape(str(exc))}[/red]", soft_wrap=True)
        raise typer.Exit(1) from exc
    console.print(f"Atalho criado: {rich_escape(str(lnk))}", soft_wrap=True)
    if info.icon is not None and not shortcut.has_usable_icon(info):
        console.print(
            "Nota: o atalho usa o ícone padrão; use um arquivo .ico para ter o ícone do app.", soft_wrap=True,
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: Optional[int] = typer.Option(
        None, "--port", help=f"Bind port (default: ${PORT_ENV} or {DEFAULT_API_PORT})",
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change"),
):
    """Inicia o servidor FastAPI via uvicorn."""
    import uvicorn
    if port is None:   # df-oift: the same port the GUI panels and the deploy CLI call
        try:
            port = api_port()
        except ValueError as e:   # df-yvgx
            Console().print(f"[red]{rich_escape(str(e))}[/red]", soft_wrap=True)
            raise typer.Exit(1) from e
    # Environment variables, so the values survive the --reload subprocess. The port also reaches
    # the app backends (DATAFORGE_API) and every client this process starts.
    set_api_port(port)
    url_host = host
    if host in ("0.0.0.0", "::"):
        os.environ[ALLOWED_HOSTS_ENV] = "*"
        url_host = "127.0.0.1"
        Console().print(
            "[yellow]Aviso: os dados ficam acessíveis na rede sem login.[/yellow]",
            soft_wrap=True,
        )
    else:
        existing = os.environ.get(ALLOWED_HOSTS_ENV)
        os.environ[ALLOWED_HOSTS_ENV] = f"{existing},{host}" if existing else host
    rprint(f"[green]Serving at http://{url_host}:{port}[/green]")
    if not list_workspaces():   # df-pr6: a wrong DATAFORGE_HOME otherwise looks like a healthy server
        Console().print(
            f"[yellow]Aviso: nenhum workspace encontrado em "
            f"{rich_escape(str(data_dir() / 'workspaces'))}. Confira o DATAFORGE_HOME.[/yellow]",
            soft_wrap=True,
        )
    for info in discover_apps():
        Console().print(
            f"  {rich_escape(info.title)}: http://{url_host}:{port}{info.url_path}",
            soft_wrap=True,
        )
    if reload:   # reload runs the server in a child process: nothing here can stop it, so no handler
        uvicorn.run("dataforge.api:app", host=host, port=port, reload=True)
        return
    server = uvicorn.Server(uvicorn.Config("dataforge.api:app", host=host, port=port))

    def _request_stop() -> None:   # runs on a request thread: only signal
        server.should_exit = True

    instance.set_shutdown_handler(_request_stop)
    try:
        server.run()
    except KeyboardInterrupt:   # as uvicorn.run: Ctrl+C is a normal stop
        pass
    finally:
        instance.set_shutdown_handler(None)
    if not server.started:   # as uvicorn.run: startup failed
        raise typer.Exit(3)


@app.command(hidden=True)
def background() -> None:
    """Processo de segundo plano: servidor, bandeja e desligamento automático."""
    from dataforge.gui.background import run_background  # noqa: PLC0415 -- keeps Qt out of CLI startup
    sys.exit(run_background())


_STOP_WAIT_SECONDS = 10


@app.command()
def stop(
    force: bool = typer.Option(False, "--force", help="Desliga mesmo com job rodando"),
) -> None:
    """Desliga o DataForge que está rodando (servidor ou segundo plano)."""
    try:
        port = api_port()
    except ValueError as e:
        Console().print(f"[red]{rich_escape(str(e))}[/red]", soft_wrap=True)
        raise typer.Exit(1) from e
    kind, body = instance.probe(port)
    if kind is instance.Probe.NONE:
        rprint("nenhum DataForge rodando")
        return
    # An older DataForge of this same home still has to be stoppable; a stranger must not be POSTed to.
    if kind is not instance.Probe.OTHER_VERSION:
        try:
            instance.refuse(kind, body, port)
        except instance.InstanceError as e:
            Console().print(f"[red]{rich_escape(str(e))}[/red]", soft_wrap=True)
            raise typer.Exit(1) from e
    try:
        response = httpx.post(
            f"http://127.0.0.1:{port}/api/instance/shutdown",
            params={"force": "true" if force else "false"},
            timeout=5.0,
            trust_env=False,
        )
    except httpx.HTTPError as e:
        Console().print(f"[red]falha ao falar com o DataForge: {rich_escape(str(e) or type(e).__name__)}[/red]", soft_wrap=True)
        raise typer.Exit(1) from e
    if response.status_code != httpx.codes.ACCEPTED:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        Console().print(f"[red]{rich_escape(str(detail))}[/red]", soft_wrap=True)
        raise typer.Exit(1)
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while instance.probe(port)[0] is not instance.Probe.NONE:
        if time.monotonic() >= deadline:
            Console().print(f"[red]o DataForge ainda responde na porta {port}[/red]", soft_wrap=True)
            raise typer.Exit(1)
        time.sleep(0.25)
    rprint("DataForge desligado")


def _say_waiting() -> None:
    """A CLI run blocked on the workspace run lock tells the user why it does not start."""
    rprint("[yellow]aguardando outro job do workspace terminar…[/yellow]")


def _report_results(results: dict, status: str) -> None:
    """Print each task's outcome; exit 1 when the run failed, so an OS scheduler sees it (df-h864)."""
    for tname, rc in results.items():
        if rc == 0:
            label = "[green]OK[/green]"
        elif rc == "skipped":
            label = "[yellow]PULADO[/yellow]"
        elif rc is None:
            label = "[yellow]ABORTADO[/yellow]"
        else:
            label = "[red]FALHOU[/red]"
        rprint(f"  {tname}: {label}")

    if status == "failed":
        raise typer.Exit(1)


@app.command()
def run(
    workspace: str = typer.Argument(..., help="Workspace name"),
    job: str = typer.Argument(..., help="Job name"),
):
    """Run a job directly without the HTTP server. Used by OS-level schedulers."""
    from dataforge.parser import load_job
    from dataforge.engine import run_job, run_status
    from dataforge.db import init_db, JobRunORM, ScheduleORM, utcnow
    from sqlalchemy.orm import Session
    import uuid

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)

    global_db_url = db_url(data_dir() / _DB_FILE)
    ws_db_url = db_url(workspace_path / _DB_FILE)
    global_engine = init_db(global_db_url)
    run_id = str(uuid.uuid4())

    with Session(global_engine) as session:
        session.add(JobRunORM(
            id=run_id, workspace=workspace, job=job,
            status="pending", started_at=utcnow(), owner_id="system",
            trigger="manual",
        ))
        sched = session.get(ScheduleORM, f"{workspace}:{job}")
        if sched:
            sched.last_run_at = utcnow()
        session.commit()

    loaded_job = load_job(workspace_path, job)
    results = run_job(loaded_job, workspace_path, ws_db_url, run_id, on_wait=_say_waiting)
    _report_results(results, run_status(loaded_job, results))


@app.command()
def scheduled_run(
    workspace: str = typer.Argument(..., help="Workspace name"),
    trigger_name: str = typer.Argument(..., help="Trigger name"),
):
    """Headless execution of a trigger — called by OS scheduler."""
    import concurrent.futures
    import uuid
    from dataforge.parser import load_trigger, load_job
    from dataforge.engine import run_job, run_status
    from dataforge.db import init_db, JobRunORM, utcnow
    from sqlalchemy.orm import Session

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)

    trigger = load_trigger(workspace_path, trigger_name)
    if not trigger.enabled:
        return

    global_db_url = db_url(data_dir() / _DB_FILE)
    # Initialize DB schema once before spawning threads (avoids concurrent create_all conflicts)
    global_engine = init_db(global_db_url)

    def _run_one(entry):
        job_ws_path = resolve_workspace(entry.workspace)
        ws_db_url = db_url(job_ws_path / _DB_FILE)
        run_id = str(uuid.uuid4())
        with Session(global_engine) as session:
            session.add(JobRunORM(
                id=run_id, workspace=entry.workspace, job=entry.job,
                status="pending", started_at=utcnow(), owner_id="system",
                trigger=trigger_name,
            ))
            session.commit()
        job = load_job(job_ws_path, entry.job)
        # Apply trigger static params as defaults; job task params override (Pitfall 6)
        for task in job.tasks:
            task.params = {**entry.params, **task.params}
        return run_status(job, run_job(job, job_ws_path, ws_db_url, run_id))

    any_failed = False
    with concurrent.futures.ThreadPoolExecutor() as ex:
        futures = [ex.submit(_run_one, entry) for entry in trigger.jobs]
        for f in concurrent.futures.as_completed(futures):
            try:
                any_failed |= f.result() == "failed"
            except Exception:
                import logging
                logging.getLogger(__name__).exception("scheduled_run: job entry failed")
                any_failed = True

    # Silent daemon notify — swallow all exceptions (D-09)
    port = api_port()
    try:
        import httpx
        httpx.post(f"http://127.0.0.1:{port}/api/triggers/notify", timeout=1.0)
    except Exception:
        pass

    # The OS scheduler only sees the exit code: a failed run must not read as success (df-h864).
    if any_failed:
        raise typer.Exit(1)


@app.command()
def schedule_sync():
    """Register all job schedules with Windows Task Scheduler."""
    from dataforge.parser import load_jobs
    from dataforge.compat import list_workspaces

    registered = 0
    for ws_dir in list_workspaces():
        for job in load_jobs(ws_dir):
            if job.schedule and _sync_job_schedule(ws_dir.name, job):
                registered += 1

    rprint(f"\n{registered} task(s) registered in Windows Task Scheduler.")


def _sync_job_schedule(workspace: str, job) -> bool:
    """schedule-sync for one scheduled job: register its Windows task unless its cron cannot be
    expressed or its schedule is disabled. Prints the outcome; True when registered."""
    label = f"{workspace}/{job.name}"
    try:
        _cron_to_ps_trigger(job.schedule)
    except ValueError as e:
        rprint(f"[yellow]skip[/yellow] {label}: {e}")
        return False
    if not _upsert_and_get_enabled(workspace, job.name, job.schedule):
        rprint(f"[dim]skip (disabled)[/dim] {label}")
        return False
    try:
        warning = _register_windows_task(workspace, job.name, job.schedule)
    except (RuntimeError, ValueError) as e:   # ValueError: unsafe name (df-utq8)
        rprint(f"[red]✗[/red] {rich_escape(workspace)}/{rich_escape(job.name)}: "
               f"{rich_escape(str(e))}")
        return False
    rprint(f"[green]✓[/green] {label} [{job.schedule}]")
    if warning:
        rprint(f"[yellow]{rich_escape(warning)}[/yellow]")
    return True


@app.command("register-triggers")
def register_triggers(
    workspace: str = typer.Argument(..., help=_WORKSPACE_HELP),
    trigger: Annotated[Optional[list[str]], typer.Option(
        "--trigger", "-t", help="Only this trigger (repeatable); default: all of them")] = None,
):
    """(Re)register the workspace's triggers with the OS scheduler, from their files.

    Enabled triggers are registered with the command of the DataForge running this, disabled ones
    are removed. On Windows, run it from an elevated terminal so the tasks run whether or not you
    are logged on (df-18s6)."""
    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{rich_escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    triggers = load_triggers(workspace_path)
    unknown = sorted(set(trigger or []) - {t.name for t in triggers})
    if unknown:
        rprint(f"[red]Unknown trigger(s): {rich_escape(', '.join(unknown))}[/red]")
        raise typer.Exit(1)
    rprint(f"[dim]command: {rich_escape(' '.join(_dataforge_command()))}[/dim]")
    ok = True
    for t in triggers:
        if not trigger or t.name in trigger:
            ok = _register_one_trigger(workspace, t) and ok
    if not ok:
        raise typer.Exit(1)


def _register_one_trigger(workspace: str, t) -> bool:
    """register-triggers for one trigger: register it if enabled, else remove its task. Prints the
    outcome and any warning; False when the OS scheduler failed."""
    label = rich_escape(f"{workspace}/{t.name}")
    try:
        if not t.enabled:
            _platform_deregister_trigger(workspace, t.name)
            rprint(f"[dim]removed (disabled)[/dim] {label}")
            return True
        _platform_check_cron(t.cron)
        warning = _platform_register_trigger(workspace, t.name, t.cron)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        rprint(f"[red]✗[/red] {label}: {rich_escape(str(e))}")
        return False
    rprint(f"[green]✓[/green] {label} [{rich_escape(t.cron)} UTC]")
    if warning:
        rprint(f"[yellow]{rich_escape(warning)}[/yellow]")
    return True


@jobs_app.command("list")
def jobs_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help=_WORKSPACE_HELP),
):
    """Lista todos os jobs do workspace."""
    from dataforge.parser import load_jobs
    from rich.table import Table

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    jobs = load_jobs(workspace_path)

    table = Table(title=f"Jobs — {workspace_path.name}")
    table.add_column("Job", style="cyan")
    table.add_column("Tasks")
    table.add_column("Retry")

    for job in jobs:
        task_names = ", ".join(t.name for t in job.tasks) or "—"
        table.add_row(
            job.name,
            task_names,
            f"{job.retry.attempts}x / {job.retry.delay_seconds}s",
        )
    console.print(table)


@jobs_app.command("run")
def jobs_run(
    job_name: str = typer.Argument(..., help="Nome do job a executar"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help=_WORKSPACE_HELP),
    db: Optional[str] = typer.Option(None, "--db"),
):
    """Roda um job específico."""
    from dataforge.parser import load_job
    from dataforge.engine import run_job, run_status

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    db_url = resolve_db(db, workspace_path)

    job = load_job(workspace_path, job_name)
    results = run_job(job, workspace_path, db_url, on_wait=_say_waiting)
    _report_results(results, run_status(job, results))


@execucoes_app.command("list")
def execucoes_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help=_WORKSPACE_HELP),
    db: Optional[str] = typer.Option(None, "--db"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """Lista histórico de execuções."""
    from dataforge.db import init_db, ExecucaoORM
    from dataforge.orphans import reconcile_task_rows  # noqa: PLC0415 -- lazy like the rest: fast start
    from sqlalchemy.orm import Session
    from rich.table import Table

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    db_url = resolve_db(db, workspace_path)

    reconcile_task_rows(db_url)   # a task whose process died is failed, not running (df-bw7)
    engine = init_db(db_url)
    with Session(engine) as session:
        rows = session.query(ExecucaoORM).order_by(ExecucaoORM.id.desc()).limit(limit).all()

    table = Table(title="Execuções")
    table.add_column("ID", style="dim")
    table.add_column("Job", style="cyan")
    table.add_column("Task", style="cyan")
    table.add_column("Status")
    table.add_column("Início")
    table.add_column("Duração (s)")

    status_color = {"success": "green", "failed": "red", "running": "yellow", "pending": "dim"}
    for row in rows:
        color = status_color.get(row.status, "white")
        table.add_row(
            str(row.id),
            row.job,
            row.task,
            f"[{color}]{row.status}[/{color}]",
            str(row.inicio)[:19] if row.inicio else "—",
            f"{row.duracao_segundos:.2f}" if row.duracao_segundos else "—",
        )
    console.print(table)


@execucoes_app.command("logs")
def execucoes_logs(
    execucao_id: int = typer.Argument(..., help="ID da execução"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help=_WORKSPACE_HELP),
    db: Optional[str] = typer.Option(None, "--db"),
):
    """Exibe os logs de uma execução: a saída (stdout e stderr) que a tarefa gravou ao terminar."""
    from dataforge.db import init_db, ExecucaoORM
    from sqlalchemy.orm import Session

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    db_url = resolve_db(db, workspace_path)

    engine = init_db(db_url)
    with Session(engine) as session:
        row = session.get(ExecucaoORM, execucao_id)

    if not row:
        rprint(f"[red]Execução #{execucao_id} não encontrada.[/red]")
        raise typer.Exit(1)
    if not row.output:
        rprint(f"[yellow]Execução #{execucao_id} não tem saída gravada "
               f"(status: {rich_escape(row.status)}).[/yellow]")
        raise typer.Exit(1)

    console.print(f"[dim]--- execução #{row.id} · {rich_escape(row.job)}/{rich_escape(row.task)}"
                  f" · {rich_escape(row.status)} ---[/dim]", soft_wrap=True)
    typer.echo(row.output.rstrip("\n"))   # as stored: not rich markup, not wrapped


if __name__ == "__main__":
    app()
