import logging
import subprocess

import croniter as croniter_module
from fastapi import APIRouter, HTTPException

from dataforge.models import TriggerModel
from dataforge.parser import delete_trigger, load_triggers, save_trigger
from dataforge.compat import resolve_workspace, valid_name
from dataforge.cli import (
    _platform_check_cron,
    _platform_deregister_trigger,
    _platform_register_trigger,
)

logger = logging.getLogger(__name__)
router = APIRouter()



def _validate_trigger_name(name: str) -> None:
    """Raise HTTP 400 if name contains path separators or other unsafe characters (T-01.5-01)."""
    if not valid_name(name):   # fullmatch: 'daily\n' is refused too (df-utq8)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trigger name '{name}': only alphanumerics, hyphens, and underscores are allowed",
        )


def _validate_cron(cron: str) -> None:
    """Raise HTTP 400 unless *cron* is valid and this machine's OS scheduler can run it. Checked
    before anything is saved or deregistered, so a refused cron changes nothing (df-vo3x.3)."""
    if not croniter_module.croniter.is_valid(cron):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: '{cron}'")
    try:
        _platform_check_cron(cron)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"The OS scheduler on this machine cannot run cron '{cron}' (UTC): {exc}",
        ) from exc


# What a failing OS scheduler command raises: crontab or launchctl exiting non-zero, a missing
# binary or an unwritable plist (OSError), Task Scheduler's RuntimeError, a translator's ValueError.
_OS_SCHEDULER_ERRORS = (subprocess.CalledProcessError, OSError, RuntimeError, ValueError)


def _register_os_trigger(ws: str, trigger: TriggerModel) -> str | None:
    """Dispatch to platform-specific OS registration. Returns an optional warning.

    Callers save the trigger file first, and the file is the source of truth: when the OS
    scheduler command fails, the result is a warning that the task is missing, not an error that
    hides the saved trigger behind a 500 (df-vo3x.6)."""
    logger.info("_register_os_trigger: ws=%s trigger=%s", ws, trigger.name)
    try:
        return _platform_register_trigger(ws, trigger.name, trigger.cron)
    except _OS_SCHEDULER_ERRORS as exc:
        logger.warning("_register_os_trigger: the OS scheduler failed (%s)", type(exc).__name__)
        return (f"Trigger saved, but the OS scheduler did not register it: {exc}. It will not "
                "run on schedule until it is saved again or reconciled.")


def _deregister_os_trigger(ws: str, trigger_name: str) -> None:
    """Dispatch to platform-specific OS deregistration. A failure is raised before the caller
    changes the trigger file, so a failed delete or update leaves both as they were."""
    logger.info("_deregister_os_trigger: ws=%s trigger=%s", ws, trigger_name)
    _platform_deregister_trigger(ws, trigger_name)


def reconcile_os_trigger(ws: str, name: str) -> str | None:
    """Make the OS scheduled task match the on-disk trigger definition: (re)register with its
    current cron if the trigger exists and is enabled, else deregister. Used after a deploy/rollback
    changes a trigger's definition — the daemon is the single OS-task writer. Idempotent; returns an
    optional registration warning.

    The deploy calls this with names taken from trigger FILES, which no route validated: an
    unsafe name is skipped with a warning, never handed to the OS scheduler (df-utq8)."""
    if not valid_name(name):
        logger.warning("reconcile_os_trigger: skipped a trigger with an invalid name")   # no raw name: S5145
        return f"invalid trigger name {name!r}: not registered with the OS scheduler"
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        return f"workspace '{ws}' not found"
    trig = next((t for t in load_triggers(workspace_dir) if t.name == name), None)
    if trig is not None and trig.enabled:
        try:
            _platform_check_cron(trig.cron)
        except ValueError as exc:
            # The file's cron cannot be scheduled here: a live task with the old cron would be
            # a schedule nobody asked for, so remove it and say why (df-vo3x.3).
            _deregister_os_trigger(ws, name)
            return f"cron '{trig.cron}' not registered with the OS scheduler: {exc}"
        return _register_os_trigger(ws, trig)
    _deregister_os_trigger(ws, name)   # gone or disabled -> ensure no stale live task
    return None


@router.get("/{ws}/triggers")
def list_triggers(ws: str):
    """Return all triggers for the workspace as a JSON list."""
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    return [t.model_dump() for t in load_triggers(workspace_dir)]


@router.post("/{ws}/triggers", status_code=201)
def create_trigger(ws: str, trigger: TriggerModel):
    """Create a trigger YAML file on disk and register with OS scheduler."""
    _validate_trigger_name(trigger.name)
    _validate_cron(trigger.cron)
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    save_trigger(workspace_dir, trigger)
    warning = _register_os_trigger(ws, trigger)
    result = trigger.model_dump()
    if warning:
        result["warnings"] = [warning]
    return result


@router.put("/{ws}/triggers/{name}")
def update_trigger(ws: str, name: str, trigger: TriggerModel):
    """Update an existing trigger: deregister old OS task, save new YAML, register new OS task."""
    _validate_trigger_name(name)
    _validate_trigger_name(trigger.name)
    _validate_cron(trigger.cron)
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    if not (workspace_dir / "triggers" / f"{name}.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Trigger '{name}' not found")
    _deregister_os_trigger(ws, name)
    save_trigger(workspace_dir, trigger)
    warning = _register_os_trigger(ws, trigger)
    result = trigger.model_dump()
    if warning:
        result["warnings"] = [warning]
    return result


@router.post("/{ws}/triggers/{name}/reconcile")
def reconcile_trigger(ws: str, name: str):
    """Re-sync the OS scheduled task with the on-disk trigger definition (called after a deploy so
    a definition change actually reaches the live schedule). The daemon is the single OS-task writer."""
    _validate_trigger_name(name)
    try:
        resolve_workspace(ws)  # 404 for an unknown workspace before touching the OS schedule
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    warning = reconcile_os_trigger(ws, name)
    result = {"reconciled": True, "trigger": name}
    if warning:
        result["warnings"] = [warning]
    return result


@router.delete("/{ws}/triggers/{name}", status_code=204)
def remove_trigger(ws: str, name: str):
    """Delete a trigger YAML file and deregister from OS scheduler."""
    _validate_trigger_name(name)
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    _deregister_os_trigger(ws, name)
    delete_trigger(workspace_dir, name)
