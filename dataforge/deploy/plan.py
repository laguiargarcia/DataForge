import hashlib
import warnings
from pathlib import Path, PurePosixPath
import yaml
from pydantic import TypeAdapter, ValidationError
from .models import DeployPlan, ItemChange, Action, Kind, PipelineModel, LEDGER_REL
from .ignore import DeployIgnore
from .pairing import (check_folder_inside, check_target_inside, normalize_target, source_equivalents,
                      target_file, windows_unsafe)

_KIND_BY_PREFIX = {
    "jobs": Kind.job,
    "scripts": Kind.script,
    "config": Kind.config,
    "triggers": Kind.trigger,
    "apps": Kind.app,
}


def kind_for(rel: str) -> Kind:
    """Kind by top folder, ignoring case: on NTFS 'TRIGGERS/x.yaml' is a trigger (df-886.18)."""
    top = rel.split("/", 1)[0].casefold()
    return _KIND_BY_PREFIX.get(top, Kind.other)


def pair_kind(*paths: str) -> Kind:
    """Kind of a file seen through its pair: a trigger when ANY of its paths is under triggers/,
    else the kind of the first. The engine reads triggers from the target, so 'config/*.yaml ->
    triggers/' lands on live triggers, and a file promoted as one keeps that care on a renamed
    target (df-7pl). Laws #1/#2 protect a trigger, so erring towards one is the safe side."""
    kinds = [kind_for(p) for p in paths]
    return Kind.trigger if Kind.trigger in kinds else kinds[0]


def _shape_conflict(target_dir: Path, tgt_rel: str) -> str | None:
    """Why a source FILE can't be written at `tgt_rel`, or None: the target has a folder at that
    path, or a file where the path needs a folder. Reported in the plan instead of crashing it
    (IsADirectoryError) or failing the apply mid-batch (df-886.13)."""
    path = target_dir / tgt_rel
    if path.exists() and not path.is_file():
        return "target has a folder where the source has a file"
    for parent in reversed(PurePosixPath(tgt_rel).parents[:-1]):   # outermost first, no '.'
        if (target_dir / parent).exists() and not (target_dir / parent).is_dir():
            return f"target has a file at {parent.as_posix()!r} where the source has a folder"
    return None


def _is_ledger_path(rel: str) -> bool:
    """True for the engine's own ledger — never a deploy/prune target (law #4). Case-insensitive:
    on NTFS 'Deploy/.ledger' IS the ledger (df-886.16)."""
    rel = rel.casefold()
    return rel == LEDGER_REL or rel.startswith(LEDGER_REL + "/")


def content_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# UnicodeDecodeError is a ValueError; the others leak from PyYAML constructors (see below).
_UNPARSEABLE = (yaml.YAMLError, ValueError, KeyError, AttributeError, IndexError, RecursionError)
# What safe_dump raises on data that safe_load accepted: nesting that recurses deeper on the way
# out, and an int whose str() exceeds the 4300-digit limit (hex/binary/base-60 load without it).
_UNDUMPABLE = (yaml.YAMLError, ValueError, RecursionError)


def _load_yaml_dict(path: Path):
    """Parse YAML to a mapping; return None if unparseable or not a mapping. Undecodable bytes
    (UnicodeDecodeError) and invalid scalars such as a date '2001-13-45' (ValueError) count as
    unparseable too (df-886.9), and so are the errors PyYAML's constructors leak on a bad explicit
    tag ('!!bool abc' -> KeyError, '!!timestamp abc' -> AttributeError, '!!int ""' -> IndexError)
    or very deep nesting (RecursionError) (df-886.17)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except _UNPARSEABLE:
        return None
    return data if isinstance(data, dict) else None


_ENABLED = TypeAdapter(bool)   # the type of TriggerModel.enabled, read the same lax way


def trigger_status(data) -> bool | None:
    """A trigger's on/off status as the engine reads it (TriggerModel.enabled: no key is on, and
    'no', 'off', 'false', 0 are off), or None when it can't be read: the file is not a mapping, or
    'enabled' is a value the engine rejects (null, a list). Never Python's bool(): bool('no') is
    True (df-886.12, df-ocwj)."""
    if data is None:
        return None
    try:
        return _ENABLED.validate_python(data.get("enabled", True))
    except ValidationError:
        return None


def _trigger_sha(path: Path) -> str:
    """Hash a trigger's definition EXCLUDING 'enabled' (law #1). Unparseable or non-mapping
    files degrade to an opaque byte hash so a dirty file can't crash the planner."""
    data = _load_yaml_dict(path)
    if data is None:
        return content_sha(path)
    data.pop("enabled", None)
    # Keys of mixed types (1 and 'a') can't be sorted; PyYAML then keeps file order, no error.
    try:
        canon = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    except _UNDUMPABLE:   # loads but won't dump back (df-886.17): compare the bytes
        return content_sha(path)
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()


def _sha(path: Path, kind: Kind) -> str:
    return _trigger_sha(path) if kind == Kind.trigger else content_sha(path)


def build_plan(
    source_dir: Path,
    target_dir: Path,
    pipeline: PipelineModel,
    pipeline_name: str,
    ignore: DeployIgnore,
    pairs: list[tuple[str, str]],
) -> DeployPlan:
    items: list[ItemChange] = []
    for src_rel, tgt_rel in pairs:
        if _is_ledger_path(tgt_rel):   # law #4 — never deploy into our own ledger
            continue
        kind = pair_kind(src_rel, tgt_rel)   # df-7pl
        if windows_unsafe(tgt_rel):   # df-886.20: skip the one file, not the whole deploy
            items.append(ItemChange(source_path=src_rel, target_path=tgt_rel, kind=kind,
                                    action=Action.skip_ignored, reason="name not valid on Windows"))
            continue
        # law #3, on both paths (df-94s): a pair may rename the folder, and prune sees both too.
        if ignore.matches(src_rel) or ignore.matches_any_case(tgt_rel):
            items.append(ItemChange(source_path=src_rel, target_path=tgt_rel,
                                    kind=kind, action=Action.skip_ignored, reason=".deployignore"))
            continue
        src_path = source_dir / src_rel
        tgt_path = target_file(target_dir, tgt_rel)   # df-m1dk: never through a linked folder
        conflict = _shape_conflict(target_dir, tgt_rel)
        if conflict:   # df-886.13: a person has to sort the target out; nothing is written
            items.append(ItemChange(source_path=src_rel, target_path=tgt_rel, kind=kind,
                                    action=Action.protected, reason=conflict))
            continue

        # Triggers (law #2'): a trigger is always promoted as its DEFINITION; the deploy never
        # changes the target's on/off status. So an active target trigger is updated in place
        # (MODIFY, enabled preserved by apply._trigger_content), a new one lands deactivated, and
        # _sha excludes 'enabled' so a pure status difference is a no-op. (Deleting a live trigger
        # is still PROTECTED — see _prune_deletes.)
        before = _sha(tgt_path, kind) if tgt_path.exists() else None
        after = _sha(src_path, kind)
        if before is None:
            items.append(ItemChange(source_path=src_rel, target_path=tgt_rel, kind=kind,
                                    action=Action.add, reason="new in target", after_sha=after))
        elif before == after:
            continue
        else:
            items.append(ItemChange(source_path=src_rel, target_path=tgt_rel, kind=kind,
                                    action=Action.modify, reason="content differs",
                                    before_sha=before, after_sha=after))

    # df-gl6: prune's "absent in source" test must use the SAME source enumeration as the
    # add/modify path — i.e. EVERY paired source file's target path, not just the ones that
    # produced a change. An in-sync file is a no-op (no ItemChange), so deriving the keep-set
    # from `items` would mark every up-to-date target as source-absent and delete it.
    source_targets = {tgt_rel for _src_rel, tgt_rel in pairs}
    items.extend(_prune_deletes(target_dir, pipeline, source_targets, pairs, ignore))
    return DeployPlan(source=source_dir.name, target=pipeline.target,
                      pipeline=pipeline_name, items=items)


def _prune_deletes(target_dir: Path, pipeline: PipelineModel,
                   source_targets: set[str], pairs: list[tuple[str, str]],
                   ignore: DeployIgnore) -> list[ItemChange]:
    """When prune=True, DELETE target files under each pair's target base that have no paired
    source counterpart. The keep-set is `source_targets` (every paired source file's target path),
    so in-sync files are preserved (df-gl6). Laws still hold: the ledger (law #4) and ignored
    paths (law #3) are never touched, and active triggers are PROTECTED (law #2). Symlinks are
    skipped. Empty-source guard: if NO source files resolved, prune is refused outright —
    empty-source-implies-delete-everything is never the intent (df-gl6)."""
    out: list[ItemChange] = []
    if not pipeline.prune:
        return out
    if not pairs:
        # No source files matched any pair rule. Pruning here would delete the ENTIRE target.
        # Refuse: a deploy with an empty source set must never be read as "delete everything".
        warnings.warn(
            "deploy prune skipped: source enumeration is EMPTY (no files matched any pair rule); "
            "pruning would delete the entire target. Check the source workspace / pair globs.",
            stacklevel=2,
        )
        return out
    for rule in pipeline.pairs:   # df-886.11: never walk a base outside the target workspace
        check_target_inside(rule.target)
    bases = {normalize_target(rule.target) for rule in pipeline.pairs}   # df-886.10
    if "" in bases:
        # df-886.14: a root target places single root files (workspace.yaml -> '.'). Walking it
        # would treat the whole workspace (.env, data/, everything unpaired) as prunable.
        warnings.warn("deploy prune skips the target root: a pair whose target is the workspace "
                      "root is never pruned.", stacklevel=2)
        bases.discard("")
    # Keys are casefolded (df-886.18): on NTFS 'JOBS/a' and 'jobs/a' are one file. On a
    # case-sensitive disk that only ever keeps a file prune could have deleted, never the reverse.
    keep = {t.casefold() for t in source_targets}
    seen: set[str] = set()   # df-886.15: nested bases ('jobs', 'jobs/sub') reach a file twice
    for base in sorted(bases):
        base_dir = target_dir / base
        if not base_dir.exists():
            continue
        for p in sorted(base_dir.rglob("*")):
            rel = p.relative_to(target_dir).as_posix()
            key = rel.casefold()
            if key in seen or key in keep or p.is_symlink() or not p.is_file():
                continue
            seen.add(key)
            item = _prune_item(p, rel, ignore, source_equivalents(pipeline, rel))
            if item is not None:
                check_folder_inside(target_dir, p.parent)   # df-m1dk: a linked base leads outside
                out.append(item)
    return out


def _prune_item(p: Path, rel: str, ignore: DeployIgnore, sources: list[str]) -> ItemChange | None:
    """The plan item for one target file that has no paired source, or None to leave it alone.
    `sources` are the source paths the pair rules map onto `rel` (pairing.source_equivalents), so
    .deployignore and the trigger rule look at both sides, as on add/modify (df-94s, df-7pl)."""
    if _is_ledger_path(rel):   # law #4
        return None
    if any(ignore.matches_any_case(r) for r in (rel, *sources)):   # law #3 (df-886.18, df-94s)
        return None
    kind = pair_kind(rel, *sources)
    if kind == Kind.trigger:   # law #2: delete a target trigger only when it is known to be off
        status = trigger_status(_load_yaml_dict(p))
        if status is None:   # df-886.12: unreadable is not off; a person has to look at it
            return ItemChange(source_path=None, target_path=rel, kind=kind, action=Action.protected,
                              reason="target trigger unreadable: status unknown")
        if status:
            return ItemChange(source_path=None, target_path=rel, kind=kind,
                              action=Action.protected, reason="target trigger is active")
    return ItemChange(source_path=None, target_path=rel, kind=kind,
                      action=Action.delete, reason="prune: absent in source",
                      before_sha=content_sha(p))
