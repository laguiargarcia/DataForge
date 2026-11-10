from enum import Enum
from typing import List, Optional
from pydantic import BaseModel

LEDGER_REL = "deploy/.ledger"   # the engine's own state — never a deploy target (law #4)


class Kind(str, Enum):
    job = "job"
    script = "script"
    config = "config"
    trigger = "trigger"
    other = "other"


class Action(str, Enum):
    add = "ADD"
    modify = "MODIFY"
    delete = "DELETE"
    skip_ignored = "SKIP_IGNORED"
    protected = "PROTECTED"


class PairRule(BaseModel):
    source: str                      # glob relative to the source workspace root
    target: str                      # target dir/path relative to the target workspace root
    promote_active: bool = False     # triggers only; law #1 default — never carry active state


class PipelineModel(BaseModel):
    target: str                      # target workspace name (resolved under workspaces/)
    description: Optional[str] = None
    pairs: List[PairRule] = []
    prune: bool = False              # items in target absent from source are left alone by default


class ItemChange(BaseModel):
    source_path: Optional[str]       # rel to source root; None for DELETE
    target_path: str                 # rel to target root
    kind: Kind
    action: Action
    reason: str = ""
    before_sha: Optional[str] = None
    after_sha: Optional[str] = None


class DeployPlan(BaseModel):
    source: str
    target: str
    pipeline: str
    items: List[ItemChange] = []

    def writable(self) -> List[ItemChange]:
        """Items that would touch disk on apply (excludes SKIP_IGNORED / PROTECTED)."""
        return [i for i in self.items if i.action in (Action.add, Action.modify, Action.delete)]

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for i in self.items:
            counts[i.action.value] = counts.get(i.action.value, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        return f"{self.source} → {self.target} [{self.pipeline}]: " + (", ".join(parts) or "no changes")


class DeployEntry(BaseModel):
    id: str
    ts: str
    source: str
    target: str
    pipeline: str
    parent: Optional[str] = None
    items: List[ItemChange] = []


class AutoDeployStatus(str, Enum):
    applied = "APPLIED"                     # one or more changes were written
    nothing_to_apply = "NOTHING_TO_APPLY"   # no writable changes (up to date, or all PROTECTED/IGNORED)


class AutoDeployResult(BaseModel):
    """Outcome of a non-interactive deploy (service.auto_deploy). Machine-readable; front-ends
    map .status to exit codes / responses."""
    source: str
    target: str
    pipeline: str
    status: AutoDeployStatus
    applied: int = 0            # items written to disk (len(entry.items); 0 when nothing applied)
    protected: int = 0          # PROTECTED items the laws kept untouched
    ignored: int = 0            # SKIP_IGNORED items
    total: int = 0              # total items in the full plan
    entry_id: Optional[str] = None   # ledger entry id, or None when nothing was applied
    triggers: List[str] = []    # names of triggers touched — front-ends reconcile their OS schedule
