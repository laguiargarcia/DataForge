import json
import os
from fnmatch import fnmatch
from pathlib import Path
from typing import List, Optional, Tuple
import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint
from dataforge.deploy import service
from dataforge.deploy.service import DeployServiceError
from dataforge.deploy.models import DeployPlan, Action, PipelineModel, AutoDeployResult, AutoDeployStatus

deploy_app = typer.Typer(help="Deploy workspace definitions from one workspace to another")
console = Console()


def _reconcile_via_daemon(target_ws: str, names: List[str], quiet: bool = False) -> None:
    """Ask the running daemon to re-sync the OS schedule for triggers a deploy/rollback touched —
    the daemon is the single OS-task writer. The files are already deployed; if the daemon is
    unreachable the live schedule may be stale, so warn (the change still landed on disk).
    `quiet` suppresses stdout chatter so `run --json` stays machine-parseable."""
    if not names:
        return
    port = int(os.environ.get("DATAFORGE_PORT", 8000))
    base = f"http://127.0.0.1:{port}/api/workspaces/{target_ws}/triggers"
    try:
        with httpx.Client(timeout=5.0) as client:
            for name in names:
                client.post(f"{base}/{name}/reconcile").raise_for_status()
        if not quiet:
            rprint(f"[dim]reconciled {len(names)} live OS schedule(s): {', '.join(names)}[/dim]")
    except Exception as e:
        if not quiet:
            rprint(f"[yellow]⚠ {len(names)} trigger(s) deployed but the live OS schedule was not "
                   f"reconciled ({e.__class__.__name__}); start the daemon or re-save in Triggers.[/yellow]")

_ACTION_STYLE = {
    Action.add: "green",
    Action.modify: "yellow",
    Action.delete: "red",
    Action.protected: "magenta",
    Action.skip_ignored: "dim",
}


def _resolve(workspace: Optional[str], pipeline_name: str) -> Tuple[Path, PipelineModel, Path]:
    """Resolve (source_dir, pipeline, target_dir); exit 1 with a clean message on any error."""
    try:
        return service.resolve(workspace, pipeline_name)
    except DeployServiceError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)


def _build(source_dir, pipeline, pipeline_name, target_dir) -> DeployPlan:
    """Build the DeployPlan for a resolved pipeline; exit 1 with a clean message on bad input."""
    try:
        return service.build(source_dir, pipeline, pipeline_name, target_dir)
    except DeployServiceError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)


def _render(plan: DeployPlan) -> None:
    if not plan.items:
        rprint("[dim]No changes.[/dim]")
        return
    table = Table(title=f"{plan.source} -> {plan.target}  \\[{plan.pipeline}]")
    table.add_column("Action")
    table.add_column("Kind")
    table.add_column("Target", overflow="fold")   # never truncate the path
    table.add_column("Why", style="dim")
    for it in plan.items:
        style = _ACTION_STYLE.get(it.action, "white")
        table.add_row(f"[{style}]{it.action.value}[/{style}]", it.kind.value, it.target_path, it.reason)
    console.print(table)


@deploy_app.command()
def diff(
    pipeline: str = typer.Argument(..., help="Pipeline name (deploy/<name>.yaml in the source workspace)"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Source workspace name"),
):
    """Show what a deploy WOULD change (dry-run — writes nothing)."""
    source_dir, pipeline_model, target_dir = _resolve(workspace, pipeline)
    _render(_build(source_dir, pipeline_model, pipeline, target_dir))


@deploy_app.command()
def apply(
    pipeline: str = typer.Argument(..., help="Pipeline name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Source workspace name"),
    only: Optional[str] = typer.Option(None, "--only",
        help="Glob over the full POSIX target path (fnmatch; matches across '/'). Deploy only matching items."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
):
    """Apply a pipeline to its target workspace (snapshots first; recoverable via rollback)."""
    source_dir, pipeline_model, target_dir = _resolve(workspace, pipeline)
    plan = _build(source_dir, pipeline_model, pipeline, target_dir)
    _render(plan)   # show the full plan (incl PROTECTED / SKIP_IGNORED) for transparency
    selected = plan.writable()
    if only is not None:
        selected = [i for i in selected if fnmatch(i.target_path, only)]
    if not selected:
        rprint("[dim]Nothing to apply.[/dim]")
        raise typer.Exit(0)
    if not yes:
        typer.confirm(f"Apply {len(selected)} change(s) to '{plan.target}'?", abort=True)
    entry = service.apply(source_dir, target_dir, plan, {i.target_path for i in selected})
    rprint(f"[green]✓[/green] Deployed {len(entry.items)} change(s) — entry {entry.id}")
    _reconcile_via_daemon(plan.target, service.trigger_names(entry))


@deploy_app.command()
def rollback(
    pipeline: str = typer.Argument(..., help="Pipeline name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Source workspace name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
):
    """Undo the most recent deploy to this pipeline's target workspace."""
    source_dir, pipeline_model, target_dir = _resolve(workspace, pipeline)
    last = service.last_entry(target_dir)
    if last is None:
        rprint("[dim]No deploy to roll back.[/dim]")
        raise typer.Exit(0)
    # The ledger is per-target, addressed here via the pipeline's target. Show full provenance
    # so a retargeted pipeline can't silently roll back a workspace the operator didn't expect.
    rprint(f"Last deploy on '{target_dir.name}': {last.source} -> {last.target} "
           f"\\[{last.pipeline}]  {last.id} — {len(last.items)} change(s)")
    if not yes:
        typer.confirm("Roll back this deploy?", abort=True)
    try:
        entry = service.rollback(target_dir)
    except FileNotFoundError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]✓[/green] Rolled back entry {entry.id}")
    _reconcile_via_daemon(entry.target, service.trigger_names(entry))


@deploy_app.command()
def status(
    pipeline: str = typer.Argument(..., help="Pipeline name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Source workspace name"),
):
    """Show the last deploy and the currently pending changes for a pipeline."""
    source_dir, pipeline_model, target_dir = _resolve(workspace, pipeline)
    last = service.last_entry(target_dir)
    if last is None:
        rprint("[dim]No deploys recorded.[/dim]")
    else:
        rprint(f"Last deploy: [cyan]{last.id}[/cyan] at {last.ts} — {len(last.items)} change(s)")
    pending = len(_build(source_dir, pipeline_model, pipeline, target_dir).writable())
    rprint(f"Pending: [yellow]{pending}[/yellow] change(s)" if pending else "[green]Up to date.[/green]")


@deploy_app.command()
def run(
    pipeline: str = typer.Argument(..., help="Pipeline name"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Source workspace name"),
    as_json: bool = typer.Option(False, "--json", help="Emit the result as JSON (machine-readable)."),
):
    """Deploy a pipeline NON-INTERACTIVELY (no prompt) — for automation/scheduling.

    Deploys every writable item; PROTECTED / IGNORED are always left untouched (the four laws).
    Exit 0 = success (changes applied OR already up to date — idempotent).
    Exit 1 = error (pipeline not found / invalid, or a write / unexpected failure).
    """
    def _fail(message: str, kind: str) -> None:
        # Headless contract: with --json, stdout stays valid JSON so a consumer never chokes on a
        # human string; otherwise a clean red line. Either way exit 1 — never a bare traceback.
        if as_json:
            typer.echo(json.dumps({"status": "ERROR", "kind": kind, "error": message}))
        else:
            rprint(f"[red]{message}[/red]")
        raise typer.Exit(1)

    try:
        result = service.auto_deploy(workspace, pipeline)
    except DeployServiceError as e:
        _fail(str(e), e.kind)
    except OSError as e:
        # D4a carry-forward: surface a write failure cleanly on the headless path. If changes were
        # partially applied a DeployEntry was recorded (D16) — recover with 'deploy rollback'.
        hint = f"deploy rollback {pipeline}" + (f" -w {workspace}" if workspace else "")
        _fail(f"Deploy failed: {e}. If changes were partially applied, run '{hint}' to recover.", "io")
    except Exception as e:  # never let the headless path emit a bare traceback / silent exit 1
        _fail(f"Unexpected error during deploy: {e}", "unexpected")

    if as_json:
        _reconcile_via_daemon(result.target, result.triggers, quiet=True)   # keep stdout = JSON only
        typer.echo(json.dumps(result.model_dump(mode="json")))
        return
    rprint(f"resolved {result.source} -> {result.target}  \\[{result.pipeline}]")
    rprint(f"applied {result.applied} / {result.total} items  "
           f"({result.protected} PROTECTED, {result.ignored} IGNORED)")
    if result.entry_id:
        rprint(f"[green]✓[/green] entry {result.entry_id}")
    elif result.protected or result.ignored:
        rprint(f"[dim]nothing applied — {result.protected} PROTECTED, "
               f"{result.ignored} IGNORED left untouched (the four laws).[/dim]")
    else:
        rprint("[dim]nothing to apply — target already up to date.[/dim]")
    _reconcile_via_daemon(result.target, result.triggers)
