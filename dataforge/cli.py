import platform
import shlex
import subprocess
import shutil
import typer
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
from pathlib import Path
from typing import Optional
from pydantic import ValidationError
from rich.console import Console
from rich import print as rprint
from dataforge.compat import resolve_workspace, resolve_db, data_dir, db_url
from dataforge.deploy.cli import deploy_app

app = typer.Typer(help="DataForge — workspace local de engenharia de dados")
jobs_app = typer.Typer(help="Gerenciar jobs")
execucoes_app = typer.Typer(help="Ver histórico de execuções")
app.add_typer(jobs_app, name="jobs")
app.add_typer(execucoes_app, name="execucoes")
app.add_typer(deploy_app, name="deploy")

console = Console()


@app.command()
def validate(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Nome do workspace (em workspaces/)"),
):
    """Valida todos os YAMLs do workspace."""
    from dataforge.parser import load_workspace, load_jobs, load_connections
    from dataforge.dag import topological_levels

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    errors = []

    try:
        ws = load_workspace(workspace_path)
        rprint(f"[green]✓[/green] workspace.yaml — {ws.name} (engine={ws.engine.value})")
    except ValidationError as e:
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"])
            errors.append(f"workspace.yaml: campo '{field}' — {err['msg']}")
    except FileNotFoundError:
        errors.append("workspace.yaml não encontrado")

    jobs = []
    try:
        jobs = load_jobs(workspace_path)
        rprint(f"[green]✓[/green] jobs/ — {len(jobs)} job(s) carregados")
        for job in jobs:
            rprint(f"  [cyan]{job.name}[/cyan] — {len(job.tasks)} task(s)")
    except ValidationError as e:
        for err in e.errors():
            field = ".".join(str(x) for x in err["loc"])
            errors.append(f"jobs/: campo '{field}' — {err['msg']}")
    except Exception as e:
        errors.append(f"jobs/: {e}")

    try:
        conns = load_connections(workspace_path)
        rprint(f"[green]✓[/green] connections.yaml — {len(conns)} conexão(ões)")
    except Exception as e:
        errors.append(f"connections.yaml: {e}")

    for job in jobs:
        try:
            topological_levels(job.tasks)
        except ValueError as e:
            errors.append(f"job '{job.name}': {e}")

        for task in job.tasks:
            if task.job:
                job_names = [j.name for j in jobs]
                if task.job not in job_names:
                    errors.append(f"job '{job.name}', task '{task.name}': job referenciado '{task.job}' não existe")

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
        f'name: {name}\nversion: "1.0"\nengine: duckdb\ndatabase: sqlite\n'
    )
    (ws_dir / "connections" / "connections.yaml").write_text(
        "connections:\n  raw_data:\n    type: parquet\n    path: ./data/raw\n"
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
    if len(parts) != 5:
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

    global_db_url = db_url(data_dir() / "dataforge.db")
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


def _is_wsl() -> bool:
    """True when running inside Windows Subsystem for Linux."""
    return "microsoft" in platform.release().lower()


def _register_crontab(workspace: str, trigger_name: str, cron: str, cmd: str) -> None:
    """Register a trigger in the user crontab (Linux). Delete+recreate contract (D-05)."""
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
    marker = f"# DataForge:{workspace}:{trigger_name}"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""
    lines = [ln for ln in existing.splitlines() if marker not in ln]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def _register_launchd(
    workspace: str, trigger_name: str, cron: str, dataforge_bin: str, home: str
) -> None:
    """Register a trigger with macOS launchd. Delete+recreate contract (D-05)."""
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {cron!r}")
    minute, hour, dom, month, dow = parts

    # Build StartCalendarInterval from cron fields (Pitfall 3)
    cal: dict = {}
    if minute != "*" and minute.isdigit():
        cal["Minute"] = int(minute)
    if hour != "*" and hour.isdigit():
        cal["Hour"] = int(hour)
    if dow != "*" and dow.isdigit():
        cal["Weekday"] = int(dow)
    elif dom != "*" and dom.isdigit():
        cal["Day"] = int(dom)

    cal_items = "\n".join(
        f"                <key>{k}</key>\n                <integer>{v}</integer>"
        for k, v in cal.items()
    )
    label = f"com.dataforge.{workspace}.{trigger_name}"
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{xml_escape(label)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{dataforge_bin}</string>
        <string>scheduled-run</string>
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
    plist_path.write_text(plist_content)

    # Delete+recreate: unload (ignore failure) then load
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)


def _deregister_launchd(workspace: str, trigger_name: str) -> None:
    """Unload and remove a launchd plist for the given trigger."""
    label = f"com.dataforge.{workspace}.{trigger_name}"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    plist_path.unlink(missing_ok=True)


def _register_windows_trigger(
    workspace: str, trigger_name: str, cron: str, dataforge_bin: str, home: str
) -> None:
    """Register a trigger with Windows Task Scheduler. Delete+recreate (D-05)."""
    trigger_args = _cron_to_ps_trigger(cron)
    task_name = f"DataForge\\{workspace}\\trigger\\{trigger_name}"

    if _is_wsl():
        wsl_cmd = (
            f"DATAFORGE_HOME={shlex.quote(str(home))} "
            f"{shlex.quote(dataforge_bin)} scheduled-run "
            f"{shlex.quote(workspace)} {shlex.quote(trigger_name)}"
        )
        ps_arg = f'-e bash -c "{wsl_cmd}"'.replace("'", "''")
        action_line = (
            f"$action = New-ScheduledTaskAction -Execute 'wsl.exe' "
            f"-Argument '{ps_arg}'"
        )
    else:
        win_cmd = (
            f"$env:DATAFORGE_HOME='{home}'; "
            f"{dataforge_bin} scheduled-run {workspace} {trigger_name}"
        )
        ps_arg = f'-NoProfile -NonInteractive -Command "{win_cmd}"'.replace("'", "''")
        action_line = (
            f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
            f"-Argument '{ps_arg}'"
        )

    ps_script = "\n".join([
        action_line,
        f"$trigger = New-ScheduledTaskTrigger {trigger_args}",
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger "
        "-Settings $settings -Force | Out-Null",
    ])
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Task Scheduler trigger registration failed:\n{result.stderr}")


def _deregister_windows_trigger(workspace: str, trigger_name: str) -> None:
    """Remove a Windows Task Scheduler task for the given trigger.

    Unregister-ScheduledTask does NOT split a path-qualified -TaskName the way
    Register-ScheduledTask does, so the folder must be passed separately as
    -TaskPath or the lookup silently misses and the task is orphaned (df-413).
    """
    task_path = f"\\DataForge\\{workspace}\\trigger\\"
    ps_script = (
        f"Unregister-ScheduledTask -TaskName '{trigger_name}' -TaskPath '{task_path}' "
        f"-Confirm:$false -ErrorAction SilentlyContinue"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
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
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", elevate_ps],
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
    """Shift cron minute/hour fields from UTC to current OS-local time.

    DataForge stores cron in UTC, but OS schedulers (schtasks, launchd, crond)
    interpret cron fields in local time, so we materialize the offset into the
    fields at registration time. Re-register after DST transitions to stay accurate.

    Plain-integer minute/hour are shifted; wildcards/ranges/lists pass through.
    Weekly DOW is shifted when the hour wraps past midnight. DOM wraparound is
    month-dependent and is not handled.
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron
    minute, hour, dom, month, dow = parts
    if not (minute.isdigit() and hour.isdigit()):
        return cron

    offset = datetime.now().astimezone().utcoffset()
    if offset is None:
        return cron
    offset_min = int(offset.total_seconds() // 60)

    total = int(hour) * 60 + int(minute) + offset_min
    day_shift, total = divmod(total, 24 * 60)
    new_hour, new_minute = divmod(total, 60)

    if day_shift != 0 and dow.isdigit():
        dow = str((int(dow) + day_shift) % 7)

    return f"{new_minute} {new_hour} {dom} {month} {dow}"


def _platform_register_trigger(workspace: str, trigger_name: str, cron: str) -> str | None:
    """Dispatch to platform-specific OS trigger registration.

    Cron is stored in UTC; OS schedulers fire in local time, so shift at the boundary.
    Returns a non-fatal warning string (or None) for the caller to surface.
    """
    import sys

    dataforge_bin = shutil.which("dataforge") or f"{sys.executable} -m dataforge"
    home = str(data_dir())
    cron = _utc_cron_to_local(cron)

    if platform.system() == "Windows" or _is_wsl():
        _register_windows_trigger(workspace, trigger_name, cron, dataforge_bin, home)
        return _ensure_task_scheduler_history()
    elif platform.system() == "Darwin":
        _register_launchd(workspace, trigger_name, cron, dataforge_bin, home)
    else:  # Linux
        cmd = (
            f"DATAFORGE_HOME={shlex.quote(str(home))} "
            f"{shlex.quote(dataforge_bin)} scheduled-run "
            f"{shlex.quote(workspace)} {shlex.quote(trigger_name)}"
        )
        _register_crontab(workspace, trigger_name, cron, cmd)


def _platform_deregister_trigger(workspace: str, trigger_name: str) -> None:
    """Dispatch to platform-specific OS trigger deregistration."""
    if platform.system() == "Windows" or _is_wsl():
        _deregister_windows_trigger(workspace, trigger_name)
    elif platform.system() == "Darwin":
        _deregister_launchd(workspace, trigger_name)
    else:  # Linux
        _deregister_crontab(workspace, trigger_name)


def _register_windows_task(workspace: str, job_name: str, cron: str) -> None:
    import sys

    dataforge_bin = shutil.which("dataforge") or f"{sys.executable} -m dataforge"
    home = str(data_dir())
    trigger_args = _cron_to_ps_trigger(cron)
    task_name = f"DataForge\\{workspace}\\{job_name}"

    if _is_wsl():
        wsl_cmd = (
            f"DATAFORGE_HOME={shlex.quote(str(home))} "
            f"{shlex.quote(dataforge_bin)} run "
            f"{shlex.quote(workspace)} {shlex.quote(job_name)}"
        )
        ps_arg = f'-e bash -c "{wsl_cmd}"'.replace("'", "''")
        action_line = f"$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument '{ps_arg}'"
    else:
        win_cmd = f"$env:DATAFORGE_HOME='{home}'; {dataforge_bin} run {workspace} {job_name}"
        ps_arg = f'-NoProfile -NonInteractive -Command "{win_cmd}"'.replace("'", "''")
        action_line = f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '{ps_arg}'"

    ps_script = "\n".join([
        action_line,
        f"$trigger = New-ScheduledTaskTrigger {trigger_args}",
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null",
    ])
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Task Scheduler registration failed:\n{result.stderr}")


@app.command()
def gui() -> None:
    """Launch the DataForge desktop application."""
    import sys
    from dataforge.gui import run_app
    sys.exit(run_app())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change"),
):
    """Inicia o servidor FastAPI via uvicorn."""
    import uvicorn
    rprint(f"[green]Serving at http://{host}:{port}[/green]")
    uvicorn.run("dataforge.api:app", host=host, port=port, reload=reload)


@app.command()
def run(
    workspace: str = typer.Argument(..., help="Workspace name"),
    job: str = typer.Argument(..., help="Job name"),
):
    """Run a job directly without the HTTP server. Used by OS-level schedulers."""
    from dataforge.parser import load_job
    from dataforge.engine import run_job
    from dataforge.db import init_db, JobRunORM, ScheduleORM
    from sqlalchemy.orm import Session
    from datetime import datetime
    import uuid, os

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)

    global_db_url = db_url(data_dir() / "dataforge.db")
    ws_db_url = db_url(workspace_path / "dataforge.db")
    global_engine = init_db(global_db_url)
    run_id = str(uuid.uuid4())

    with Session(global_engine) as session:
        session.add(JobRunORM(
            id=run_id, workspace=workspace, job=job,
            status="pending", started_at=datetime.utcnow(), owner_id="system",
            trigger="manual",
        ))
        sched = session.get(ScheduleORM, f"{workspace}:{job}")
        if sched:
            sched.last_run_at = datetime.utcnow()
        session.commit()

    loaded_job = load_job(workspace_path, job)
    results = run_job(loaded_job, workspace_path, ws_db_url, run_id)

    for tname, rc in results.items():
        if rc == 0:
            status = "[green]OK[/green]"
        elif rc == "skipped":
            status = "[yellow]PULADO[/yellow]"
        elif rc is None:
            status = "[yellow]ABORTADO[/yellow]"
        else:
            status = "[red]FALHOU[/red]"
        rprint(f"  {tname}: {status}")

    if any(isinstance(rc, int) and rc != 0 for rc in results.values()):
        raise typer.Exit(1)


@app.command()
def scheduled_run(
    workspace: str = typer.Argument(..., help="Workspace name"),
    trigger_name: str = typer.Argument(..., help="Trigger name"),
):
    """Headless execution of a trigger — called by OS scheduler."""
    import concurrent.futures
    import uuid
    import os
    from datetime import datetime
    from dataforge.parser import load_trigger, load_job
    from dataforge.engine import run_job
    from dataforge.db import init_db, JobRunORM
    from sqlalchemy.orm import Session

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)

    trigger = load_trigger(workspace_path, trigger_name)
    if not trigger.enabled:
        return

    global_db_url = db_url(data_dir() / "dataforge.db")
    # Initialize DB schema once before spawning threads (avoids concurrent create_all conflicts)
    global_engine = init_db(global_db_url)

    def _run_one(entry):
        job_ws_path = resolve_workspace(entry.workspace)
        ws_db_url = db_url(job_ws_path / "dataforge.db")
        run_id = str(uuid.uuid4())
        with Session(global_engine) as session:
            session.add(JobRunORM(
                id=run_id, workspace=entry.workspace, job=entry.job,
                status="pending", started_at=datetime.utcnow(), owner_id="system",
                trigger=trigger_name,
            ))
            session.commit()
        job = load_job(job_ws_path, entry.job)
        # Apply trigger static params as defaults; job task params override (Pitfall 6)
        for task in job.tasks:
            task.params = {**entry.params, **task.params}
        run_job(job, job_ws_path, ws_db_url, run_id)

    with concurrent.futures.ThreadPoolExecutor() as ex:
        futures = [ex.submit(_run_one, entry) for entry in trigger.jobs]
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error(
                    "scheduled_run: job entry failed: %s", exc, exc_info=True
                )

    # Silent daemon notify — swallow all exceptions (D-09)
    port = int(os.environ.get("DATAFORGE_PORT", 8000))
    try:
        import httpx
        httpx.post(f"http://127.0.0.1:{port}/api/triggers/notify", timeout=1.0)
    except Exception:
        pass


@app.command()
def schedule_sync():
    """Register all job schedules with Windows Task Scheduler."""
    from dataforge.parser import load_jobs
    from dataforge.compat import list_workspaces

    registered = 0
    for ws_dir in list_workspaces():
        for job in load_jobs(ws_dir):
            if not job.schedule:
                continue
            try:
                _cron_to_ps_trigger(job.schedule)
            except ValueError as e:
                rprint(f"[yellow]skip[/yellow] {ws_dir.name}/{job.name}: {e}")
                continue

            enabled = _upsert_and_get_enabled(ws_dir.name, job.name, job.schedule)
            if not enabled:
                rprint(f"[dim]skip (disabled)[/dim] {ws_dir.name}/{job.name}")
                continue

            try:
                _register_windows_task(ws_dir.name, job.name, job.schedule)
                rprint(f"[green]✓[/green] {ws_dir.name}/{job.name} [{job.schedule}]")
                registered += 1
            except RuntimeError as e:
                rprint(f"[red]✗[/red] {ws_dir.name}/{job.name}: {e}")

    rprint(f"\n{registered} task(s) registered in Windows Task Scheduler.")


@jobs_app.command("list")
def jobs_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Nome do workspace (em workspaces/)"),
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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Nome do workspace (em workspaces/)"),
    db: Optional[str] = typer.Option(None, "--db"),
):
    """Roda um job específico."""
    from dataforge.parser import load_job
    from dataforge.engine import run_job

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    db_url = resolve_db(db, workspace_path)

    job = load_job(workspace_path, job_name)
    results = run_job(job, workspace_path, db_url)

    for tname, rc in results.items():
        if rc == 0:
            status = "[green]OK[/green]"
        elif rc == "skipped":
            status = "[yellow]PULADO[/yellow]"
        elif rc is None:
            status = "[yellow]ABORTADO[/yellow]"
        else:
            status = "[red]FALHOU[/red]"
        rprint(f"  {tname}: {status}")

    if any(isinstance(rc, int) and rc != 0 for rc in results.values()):
        raise typer.Exit(1)


@execucoes_app.command("list")
def execucoes_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Nome do workspace (em workspaces/)"),
    db: Optional[str] = typer.Option(None, "--db"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """Lista histórico de execuções."""
    from dataforge.db import init_db, ExecucaoORM
    from sqlalchemy.orm import Session
    from rich.table import Table

    try:
        workspace_path = resolve_workspace(workspace)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    db_url = resolve_db(db, workspace_path)

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
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Nome do workspace (em workspaces/)"),
    db: Optional[str] = typer.Option(None, "--db"),
):
    """Exibe os logs de uma execução."""
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

    log_dir = workspace_path / "logs"
    matches = list(log_dir.glob(f"execucao_{execucao_id}_*.log"))
    if not matches:
        rprint(f"[red]Log não encontrado para execução #{execucao_id}.[/red]")
        raise typer.Exit(1)

    rprint(f"[dim]--- {matches[0].name} ---[/dim]")
    console.print(matches[0].read_text())


if __name__ == "__main__":
    app()
