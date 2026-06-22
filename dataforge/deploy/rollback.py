from pathlib import Path
from .models import DeployEntry, Action
from .ledger import Ledger


def _prune_empty_dirs(target_dir: Path, leaf_dir: Path) -> None:
    """Remove directories a rollback-of-ADD left empty, walking up from `leaf_dir` toward
    `target_dir`. Stops at the first non-empty directory and never removes `target_dir` itself or
    anything outside it. `rmdir` only deletes an empty directory, so a dir that still holds files
    (or the never-empty ledger) is always left intact."""
    target_dir = target_dir.resolve()
    d = leaf_dir.resolve()
    while d != target_dir and target_dir in d.parents:
        try:
            d.rmdir()           # succeeds only if d is empty
        except OSError:
            break               # non-empty, or already gone — stop walking up
        d = d.parent


def rollback_last(target_dir: Path, ledger: Ledger) -> DeployEntry | None:
    """Undo the most recent deploy: ADDs are removed (and any directories they leave empty are
    pruned), MODIFY/DELETE are restored from the snapshotted 'before' blob, then the entry is
    dropped (LIFO). Pre-flights that every needed snapshot blob exists, so a missing blob aborts
    before any file is touched (never a partial restore). Returns the rolled-back entry, or None
    if the log was empty."""
    entry = ledger.last()
    if entry is None:
        return None
    for item in entry.items:
        if item.action != Action.add and item.before_sha and not ledger.has_blob(item.before_sha):
            raise FileNotFoundError(
                f"rollback aborted: missing snapshot {item.before_sha} for {item.target_path}")
    emptied: set[Path] = set()
    for item in entry.items:
        tgt = target_dir / item.target_path
        if item.action == Action.add:
            if tgt.exists():
                tgt.unlink()
            emptied.add(tgt.parent)
        elif item.before_sha:   # MODIFY or DELETE — restore snapshot
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_bytes(ledger.read_blob(item.before_sha))
    # Prune dirs the removed ADDs left empty — deepest first so nested children clear before
    # their parents are tested. Stops at any dir that still holds a file (e.g. a restored MODIFY).
    for d in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
        _prune_empty_dirs(target_dir, d)
    ledger.drop_last()
    return entry
