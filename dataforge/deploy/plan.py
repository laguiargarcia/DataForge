import hashlib
import warnings
from pathlib import Path
import yaml
from .models import DeployPlan, ItemChange, Action, Kind, PipelineModel, LEDGER_REL
from .ignore import DeployIgnore

_KIND_BY_PREFIX = {
    "jobs": Kind.job,
    "scripts": Kind.script,
    "config": Kind.config,
    "triggers": Kind.trigger,
}


def kind_for(rel: str) -> Kind:
    top = rel.split("/", 1)[0]
    return _KIND_BY_PREFIX.get(top, Kind.other)


def _is_ledger_path(rel: str) -> bool:
    """True for the engine's own ledger — never a deploy/prune target (law #4)."""
    return rel == LEDGER_REL or rel.startswith(LEDGER_REL + "/")


def content_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml_dict(path: Path):
    """Parse YAML to a mapping; return None if unparseable or not a mapping."""
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _trigger_sha(path: Path) -> str:
    """Hash a trigger's definition EXCLUDING 'enabled' (law #1). Unparseable or non-mapping
    files degrade to an opaque byte hash so a dirty file can't crash the planner."""
    data = _load_yaml_dict(path)
    if data is None:
        return content_sha(path)
    data.pop("enabled", None)
    canon = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
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
        kind = kind_for(src_rel)
        src_path = source_dir / src_rel
        tgt_path = target_dir / tgt_rel

        if ignore.matches(src_rel):    # law #3
            items.append(ItemChange(source_path=src_rel, target_path=tgt_rel,
                                    kind=kind, action=Action.skip_ignored, reason=".deployignore"))
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
    bases = {rule.target.rstrip("/") for rule in pipeline.pairs}
    for base in sorted(bases):
        base_dir = target_dir / base
        if not base_dir.exists():
            continue
        for p in sorted(base_dir.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(target_dir).as_posix()
            if rel in source_targets:   # has a paired source counterpart — keep it
                continue
            if _is_ledger_path(rel):   # law #4 — never prune our own ledger
                continue
            if ignore.matches(rel):    # law #3
                continue
            kind = kind_for(rel)
            if kind == Kind.trigger:   # law #2
                tdata = _load_yaml_dict(p)
                if tdata is not None and tdata.get("enabled", False):
                    out.append(ItemChange(source_path=None, target_path=rel, kind=kind,
                                          action=Action.protected, reason="target trigger is active"))
                    continue
            out.append(ItemChange(source_path=None, target_path=rel, kind=kind,
                                  action=Action.delete, reason="prune: absent in source",
                                  before_sha=content_sha(p)))
    return out
