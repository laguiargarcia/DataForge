"""Transport-neutral deploy facade: the single orchestration layer the CLI, the HTTP API, and
auto-deploy (D5) all call. Front-ends translate DeployServiceError into their own error surface."""
from pathlib import Path
from typing import List, Optional, Set, Tuple

import yaml
from pydantic import ValidationError

from dataforge.compat import resolve_workspace
from dataforge.deploy.models import (
    DeployPlan, DeployEntry, PipelineModel, Action, Kind, AutoDeployResult, AutoDeployStatus,
)
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.apply import apply_plan
from dataforge.deploy.rollback import rollback_last


class DeployServiceError(Exception):
    """Transport-neutral deploy error. `kind` is 'not_found' or 'invalid'."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def list_pipelines(source_dir: Path) -> List[str]:
    """Sorted pipeline names = stems of deploy/*.yaml. Never parses a file, so a malformed
    pipeline can't break enumeration (degrade, never crash)."""
    deploy_dir = source_dir / "deploy"
    if not deploy_dir.is_dir():
        return []
    return sorted(p.stem for p in deploy_dir.glob("*.yaml") if p.is_file())


def resolve(workspace: Optional[str], pipeline_name: str) -> Tuple[Path, PipelineModel, Path]:
    """Resolve (source_dir, pipeline, target_dir). Raises DeployServiceError on any failure."""
    try:
        source_dir = resolve_workspace(workspace)
    except ValueError as e:
        raise DeployServiceError(str(e), "not_found")
    try:
        pipeline = load_pipeline(source_dir, pipeline_name)
    except FileNotFoundError:
        raise DeployServiceError(
            f"Pipeline '{pipeline_name}' not found in {source_dir}/deploy/", "not_found")
    except (yaml.YAMLError, ValidationError, TypeError) as e:
        raise DeployServiceError(f"Pipeline '{pipeline_name}' is invalid: {e}", "invalid")
    try:
        target_dir = resolve_workspace(pipeline.target)
    except ValueError as e:
        raise DeployServiceError(str(e), "not_found")
    return source_dir, pipeline, target_dir


def build(source_dir: Path, pipeline: PipelineModel, pipeline_name: str,
          target_dir: Path) -> DeployPlan:
    """Build the DeployPlan. A pair target that escapes the workspace → invalid."""
    ignore = DeployIgnore.load(source_dir)
    try:
        pairs = resolve_pairs(source_dir, pipeline)
        return build_plan(source_dir, target_dir, pipeline, pipeline_name, ignore, pairs)
    except ValueError as e:   # bad pipeline input (e.g. a pair target escaping the workspace)
        raise DeployServiceError(str(e), "invalid")


def apply(source_dir: Path, target_dir: Path, plan: DeployPlan,
          only: Optional[Set[str]]) -> Optional[DeployEntry]:
    """Apply the plan's writable items, optionally filtered to target paths in `only`
    (all writable items if `only` is None). Returns None when nothing is selected, so no empty
    ledger entry is written. PROTECTED / SKIP_IGNORED items are never in writable(), so the four
    laws hold even if their paths are passed in `only`."""
    selected = plan.writable()
    if only is not None:
        selected = [i for i in selected if i.target_path in only]
    if not selected:
        return None
    selected_plan = DeployPlan(source=plan.source, target=plan.target,
                               pipeline=plan.pipeline, items=selected)
    return apply_plan(source_dir, target_dir, selected_plan, Ledger(target_dir))


def rollback(target_dir: Path) -> Optional[DeployEntry]:
    """Undo the target's most recent deploy (LIFO). None if nothing to roll back."""
    return rollback_last(target_dir, Ledger(target_dir))


def last_entry(target_dir: Path) -> Optional[DeployEntry]:
    """Most recent ledger entry for target_dir; None if no deploys are recorded."""
    return Ledger(target_dir).last()


def trigger_names(entry: Optional[DeployEntry]) -> List[str]:
    """Names of triggers (stems of trigger items under `triggers/`) touched by a deploy/rollback
    entry. Front-ends pass these to the daemon's trigger-reconcile so the live OS schedule matches
    the deployed definition. Renamed-to-non-`triggers/` targets are skipped — they aren't OS-managed."""
    if entry is None:
        return []
    return [Path(i.target_path).stem for i in entry.items
            if i.kind == Kind.trigger
            and i.target_path.startswith("triggers/") and i.target_path.endswith(".yaml")]


def auto_deploy(workspace: Optional[str], pipeline_name: str,
                only: Optional[Set[str]] = None) -> AutoDeployResult:
    """Non-interactive deploy: resolve -> build -> apply in one call, honoring the four laws.
    `apply` only ever writes plan.writable(), so PROTECTED / SKIP_IGNORED items can never be
    touched (laws #2/#3). Raises DeployServiceError (kind 'not_found'/'invalid') on a bad
    pipeline/workspace. An OSError from the write phase propagates — a partial DeployEntry is
    already recorded (D16), so the deploy stays recoverable."""
    source_dir, pipeline, target_dir = resolve(workspace, pipeline_name)
    plan = build(source_dir, pipeline, pipeline_name, target_dir)
    protected = sum(1 for i in plan.items if i.action == Action.protected)
    ignored = sum(1 for i in plan.items if i.action == Action.skip_ignored)
    entry = apply(source_dir, target_dir, plan, only)
    if entry is None:
        return AutoDeployResult(
            source=plan.source, target=plan.target, pipeline=plan.pipeline,
            status=AutoDeployStatus.nothing_to_apply,
            applied=0, protected=protected, ignored=ignored, total=len(plan.items), entry_id=None)
    return AutoDeployResult(
        source=plan.source, target=plan.target, pipeline=plan.pipeline,
        status=AutoDeployStatus.applied,
        applied=len(entry.items), protected=protected, ignored=ignored,
        total=len(plan.items), entry_id=entry.id, triggers=trigger_names(entry))
