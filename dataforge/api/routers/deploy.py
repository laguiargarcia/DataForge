from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dataforge.compat import resolve_workspace
from dataforge.deploy import service
from dataforge.deploy.service import DeployServiceError
from dataforge.api.routers.triggers import reconcile_os_trigger

router = APIRouter()


def _reconcile_triggers(target_ws: str, entry) -> list:
    """After a deploy/rollback, re-sync the OS schedule for every trigger the entry touched — the
    daemon is the single OS-task writer. Best-effort: a registration warning is surfaced, not raised
    (the files are already deployed). Returns the list of reconciled trigger names."""
    done = []
    for name in service.trigger_names(entry):
        reconcile_os_trigger(target_ws, name)
        done.append(name)
    return done


class ApplyRequest(BaseModel):
    only: Optional[List[str]] = None


def _resolve_or_http(ws: str, pipeline: str):
    try:
        return service.resolve(ws, pipeline)
    except DeployServiceError as e:
        raise HTTPException(status_code=404 if e.kind == "not_found" else 400, detail=str(e))


def _build_or_http(source_dir, pipeline, pipeline_name, target_dir):
    try:
        return service.build(source_dir, pipeline, pipeline_name, target_dir)
    except DeployServiceError as e:
        raise HTTPException(status_code=404 if e.kind == "not_found" else 400, detail=str(e))


@router.get("/{ws}/deploy/pipelines")
def list_pipelines(ws: str):
    """List pipeline names (deploy/*.yaml stems) for the source workspace."""
    try:
        source_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    return service.list_pipelines(source_dir)


@router.get("/{ws}/deploy/{pipeline}/plan")
def get_plan(ws: str, pipeline: str):
    """Compute the DeployPlan (diff/dry-run — writes nothing)."""
    source_dir, pipeline_model, target_dir = _resolve_or_http(ws, pipeline)
    return _build_or_http(source_dir, pipeline_model, pipeline, target_dir).model_dump()


@router.get("/{ws}/deploy/{pipeline}/status")
def get_status(ws: str, pipeline: str):
    """Last deploy entry for the target + count of pending writable changes."""
    source_dir, pipeline_model, target_dir = _resolve_or_http(ws, pipeline)
    plan = _build_or_http(source_dir, pipeline_model, pipeline, target_dir)
    last = service.last_entry(target_dir)
    return {"last": last.model_dump() if last else None, "pending": len(plan.writable())}


@router.post("/{ws}/deploy/{pipeline}/apply")
def apply_pipeline(ws: str, pipeline: str, body: Optional[ApplyRequest] = None):
    """Apply the pipeline (snapshots first; recoverable). `only` = explicit target paths to
    deploy (omit/null = all writable). PROTECTED/IGNORED items can never be forced."""
    source_dir, pipeline_model, target_dir = _resolve_or_http(ws, pipeline)
    plan = _build_or_http(source_dir, pipeline_model, pipeline, target_dir)
    only = set(body.only) if (body is not None and body.only is not None) else None
    entry = service.apply(source_dir, target_dir, plan, only)
    reconciled = _reconcile_triggers(pipeline_model.target, entry)
    return {"applied": entry is not None, "entry": entry.model_dump() if entry else None,
            "reconciled_triggers": reconciled}


@router.post("/{ws}/deploy/{pipeline}/rollback")
def rollback_pipeline(ws: str, pipeline: str):
    """Undo the target's most recent deploy (LIFO). 409 if there is nothing to roll back;
    500 if a snapshot blob is missing (D18 — ledger corruption, not a client conflict)."""
    _, _, target_dir = _resolve_or_http(ws, pipeline)
    try:
        entry = service.rollback(target_dir)   # single read; raises on a missing snapshot blob
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=409, detail="No deploy to roll back")
    _reconcile_triggers(entry.target, entry)
    return entry.model_dump()
