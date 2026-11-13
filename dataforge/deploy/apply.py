import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
import yaml
from .models import DeployPlan, ItemChange, DeployEntry, Action, Kind, EntryStatus
from .ledger import Ledger, atomic_write, blob_sha
from .pairing import target_file
from .plan import _UNDUMPABLE, _load_yaml_dict, _is_ledger_path, trigger_status

_log = logging.getLogger(__name__)


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


def _current_sha(path: Path) -> str | None:
    return blob_sha(path.read_bytes()) if path.is_file() else None


def _content(source_dir: Path, item: ItemChange, tgt: Path, before: str | None) -> bytes:
    if item.kind == Kind.trigger:
        # Keep the status the engine reads (df-886.8, df-ocwj); one it can't read is written
        # off, like a new trigger (D14).
        prior_enabled = bool(before is not None and trigger_status(_load_yaml_dict(tgt)))
        return _trigger_content(source_dir / item.source_path, prior_enabled)
    return (source_dir / item.source_path).read_bytes()


def _prepare(source_dir: Path, checked: list[tuple[ItemChange, Path]],
             ledger: Ledger) -> list[tuple[Path, bytes | None, ItemChange]]:
    """(target, content to write or None to delete, change) for every item, with every blob the
    batch needs already in the ledger, before any target is touched (df-ahua.5.1)."""
    steps: list[tuple[Path, bytes | None, ItemChange]] = []
    for item, tgt in checked:
        before = ledger.write_blob(tgt.read_bytes()) if tgt.exists() else None
        if item.action == Action.delete:
            steps.append((tgt, None, ItemChange(source_path=None, target_path=item.target_path,
                                                kind=item.kind, action=Action.delete,
                                                reason=item.reason, before_sha=before)))
            continue
        content = _content(source_dir, item, tgt, before)
        steps.append((tgt, content, ItemChange(
            source_path=item.source_path, target_path=item.target_path, kind=item.kind,
            action=item.action, reason=item.reason, before_sha=before,
            after_sha=ledger.write_blob(content))))
    return steps


def _record(ledger: Ledger, entry: DeployEntry, written: list[ItemChange]) -> DeployEntry | None:
    """Turn the unfinished last entry into the record of what reached the target (D16): complete,
    with only those items. With none it is dropped, as an apply that wrote nothing records nothing.
    Returns the log's last entry afterwards."""
    if not written:
        ledger.drop_last()
        return ledger.last()
    done = entry.model_copy(update={"items": written, "status": EntryStatus.complete})
    ledger.replace_last(done)
    return done


def _settle_unfinished(target_dir: Path, ledger: Ledger) -> DeployEntry | None:
    """The last entry, after recording what an apply that died left behind (df-ahua.5.1): of its
    planned items, those whose target no longer holds the pre-deploy content reached it. So a
    killed apply ends up recorded as an exception would have recorded it."""
    last = ledger.last()
    if last is None or last.status != EntryStatus.in_progress:
        return last
    written = [item for item in last.items
               if _current_sha(target_file(target_dir, item.target_path)) != item.before_sha]
    _log.warning("deploy %s did not finish; recording the %d of its %d change(s) that reached "
                 "the target", last.id, len(written), len(last.items))
    return _record(ledger, last, written)


def apply_plan(source_dir: Path, target_dir: Path, plan: DeployPlan, ledger: Ledger) -> DeployEntry:
    """Execute the writable items of a plan, write-ahead (df-ahua.5.1): snapshot every
    overwrite/delete and store every new content in the ledger, append the entry as in_progress
    listing all of them, write each target atomically, then mark the entry complete. If a write
    raises, the entry keeps only the items written; if the process dies, the next apply records
    the same from what is on disk, and rollback can undo the unfinished entry as it stands. So a
    partial apply stays rollback-recoverable (law #4). The engine's own ledger is never a target.
    Returns the entry (raising afterwards if the apply failed)."""
    # law #4 — never touch our own ledger. Every path is checked before the first write, so a plan
    # with a path outside the target is refused whole, with nothing applied (df-m1dk).
    checked = [(item, target_file(target_dir, item.target_path))
               for item in plan.writable() if not _is_ledger_path(item.target_path)]
    prev = _settle_unfinished(target_dir, ledger)
    steps = _prepare(source_dir, checked, ledger)
    now = datetime.now(timezone.utc)
    entry = DeployEntry(id=now.strftime("dpl_%Y%m%dT%H%M%S_") + uuid.uuid4().hex[:6],
                        ts=now.isoformat(), parent=(prev.id if prev else None),
                        source=plan.source, target=plan.target, pipeline=plan.pipeline,
                        items=[change for _, _, change in steps], status=EntryStatus.in_progress)
    if not steps:
        return entry.model_copy(update={"status": EntryStatus.complete})
    ledger.append(entry)   # on disk before the first target write
    written: list[ItemChange] = []
    try:
        for tgt, content, change in steps:
            if content is None:
                tgt.unlink()
            else:
                atomic_write(tgt, content)
            written.append(change)
    finally:   # also when a write raised: record what actually hit disk, then propagate
        done = _record(ledger, entry, written)
    return done
