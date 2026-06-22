import logging
import re
from pathlib import Path

import croniter as croniter_module
from fastapi import APIRouter, HTTPException

from dataforge.models import TriggerModel
from dataforge.parser import delete_trigger, load_triggers, save_trigger
from dataforge.compat import resolve_workspace
from dataforge.cli import _platform_register_trigger, _platform_deregister_trigger

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_TRIGGER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_trigger_name(name: str) -> None:
    """Raise HTTP 400 if name contains path separators or other unsafe characters (T-01.5-01)."""
    if not _VALID_TRIGGER_NAME.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trigger name '{name}': only alphanumerics, hyphens, and underscores are allowed",
        )


def _register_os_trigger(ws: str, trigger: TriggerModel) -> str | None:
    """Dispatch to platform-specific OS registration. Returns an optional warning."""
    logger.info("_register_os_trigger: ws=%s trigger=%s", ws, trigger.name)
    return _platform_register_trigger(ws, trigger.name, trigger.cron)


def _deregister_os_trigger(ws: str, trigger_name: str) -> None:
    """Dispatch to platform-specific OS deregistration. Errors are logged, not raised."""
    logger.info("_deregister_os_trigger: ws=%s trigger=%s", ws, trigger_name)
    _platform_deregister_trigger(ws, trigger_name)


def reconcile_os_trigger(ws: str, name: str) -> str | None:
    """Make the OS scheduled task match the on-disk trigger definition: (re)register with its
    current cron if the trigger exists and is enabled, else deregister. Used after a deploy/rollback
    changes a trigger's definition — the daemon is the single OS-task writer. Idempotent; returns an
    optional registration warning."""
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        return f"workspace '{ws}' not found"
    trig = next((t for t in load_triggers(workspace_dir) if t.name == name), None)
    if trig is not None and trig.enabled:
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
    if not (workspace_dir / "workspace.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    return [t.model_dump() for t in load_triggers(workspace_dir)]


@router.post("/{ws}/triggers", status_code=201)
def create_trigger(ws: str, trigger: TriggerModel):
    """Create a trigger YAML file on disk and register with OS scheduler."""
    _validate_trigger_name(trigger.name)
    if not croniter_module.croniter.is_valid(trigger.cron):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: '{trigger.cron}'")
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    if not (workspace_dir / "workspace.yaml").exists():
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
    if not croniter_module.croniter.is_valid(trigger.cron):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: '{trigger.cron}'")
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    if not (workspace_dir / "workspace.yaml").exists():
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
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    if not (workspace_dir / "workspace.yaml").exists():
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
    if not (workspace_dir / "workspace.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    _deregister_os_trigger(ws, name)
    delete_trigger(workspace_dir, name)
