import uuid
from datetime import datetime, timezone
from pathlib import Path
import yaml
from .models import DeployPlan, ItemChange, DeployEntry, Action, Kind
from .ledger import Ledger, atomic_write
from .pairing import target_file
from .plan import _UNDUMPABLE, _load_yaml_dict, _is_ledger_path, trigger_status


def _trigger_content(source_path: Path, prior_enabled: bool) -> bytes:
    """Promote a trigger's definition but keep the target's 'enabled' (law #1). A source that
    isn't a YAML mapping is copied verbatim (degrade, never crash — D14)."""
    sdata = _load_yaml_dict(source_path)
    if sdata is None:
        return source_path.read_bytes()
    sdata["enabled"] = prior_enabled
    try:
        return yaml.safe_dump(sdata, allow_unicode=True, sort_keys=False).encode()
    except _UNDUMPABLE:   # loads but won't dump back (df-886.17): copy it verbatim
        return source_path.read_bytes()


def apply_plan(source_dir: Path, target_dir: Path, plan: DeployPlan, ledger: Ledger) -> DeployEntry:
    """Execute the writable items of a plan: snapshot every overwrite/delete into the ledger
    first, write atomically, and record a DeployEntry. The entry is recorded even if a write
    fails mid-batch, so a partial apply stays rollback-recoverable (law #4). The engine's own
    ledger is never a target. Returns the entry (raising afterwards if the apply failed)."""
    # law #4 — never touch our own ledger. Every path is checked before the first write, so a plan
    # with a path outside the target is refused whole, with nothing applied (df-m1dk).
    checked = [(item, target_file(target_dir, item.target_path))
               for item in plan.writable() if not _is_ledger_path(item.target_path)]
    prev = ledger.last()
    applied: list[ItemChange] = []
    error: Exception | None = None
    try:
        for item, tgt in checked:
            before = ledger.write_blob(tgt.read_bytes()) if tgt.exists() else None

            if item.action == Action.delete:
                tgt.unlink()
                applied.append(ItemChange(source_path=None, target_path=item.target_path,
                                          kind=item.kind, action=Action.delete,
                                          reason=item.reason, before_sha=before))
                continue

            if item.kind == Kind.trigger:
                # Keep the status the engine reads (df-886.8, df-ocwj); one it can't read is
                # written off, like a new trigger (D14).
                prior_enabled = bool(before is not None and trigger_status(_load_yaml_dict(tgt)))
                content = _trigger_content(source_dir / item.source_path, prior_enabled)
            else:
                content = (source_dir / item.source_path).read_bytes()

            atomic_write(tgt, content)
            after = ledger.write_blob(content)
            applied.append(ItemChange(source_path=item.source_path, target_path=item.target_path,
                                      kind=item.kind, action=item.action, reason=item.reason,
                                      before_sha=before, after_sha=after))
    except Exception as e:   # record what actually hit disk, then propagate
        error = e

    now = datetime.now(timezone.utc)
    entry = DeployEntry(id=now.strftime("dpl_%Y%m%dT%H%M%S_") + uuid.uuid4().hex[:6],
                        ts=now.isoformat(), parent=(prev.id if prev else None),
                        source=plan.source, target=plan.target, pipeline=plan.pipeline,
                        items=applied)
    if applied:
        ledger.append(entry)
    if error is not None:
        raise error
    return entry
