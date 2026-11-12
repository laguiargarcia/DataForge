import posixpath
from pathlib import Path, PurePosixPath
import yaml
from .models import PipelineModel


def load_pipeline(workspace_dir: Path, name: str) -> PipelineModel:
    path = workspace_dir / "deploy" / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return PipelineModel(**data)


def source_base(pattern: str) -> str:
    """Leading path segments of a glob before the first wildcard segment."""
    base: list[str] = []
    for part in pattern.split("/"):
        if any(c in part for c in "*?["):
            break
        base.append(part)
    return "/".join(base)


def windows_unsafe(rel: str) -> bool:
    """True if Windows would read `rel` as a different path: '\\' is a separator there, ':' a drive
    or an alternate data stream ('x::$INDEX_ALLOCATION' is the folder x), and Win32 drops a
    trailing dot or space from a segment ('deploy./.ledger' IS deploy/.ledger) (df-886.16/.19)."""
    if "\\" in rel or ":" in rel:
        return True
    return any(seg not in ("", ".", "..") and seg[-1] in ". " for seg in rel.split("/"))


def _escapes(rel: str) -> bool:
    return posixpath.isabs(rel) or ".." in PurePosixPath(rel).parts


def check_target_inside(target_rel: str) -> None:
    """Raise ValueError if a pair target escapes the target workspace: absolute, with '..', or
    spelled so that Windows reads it as another path (see windows_unsafe). Targets are POSIX."""
    if _escapes(target_rel) or windows_unsafe(target_rel):
        raise ValueError(f"deploy pair target escapes target workspace: {target_rel!r}")


def check_folder_inside(target_dir: Path, folder: Path) -> None:
    """Raise ValueError if `folder`, with symlinks and junctions resolved, is not the target
    workspace or inside it. check_target_inside only reads the spelling; a folder of the target
    that is a link to somewhere else passes it and still leads outside (df-m1dk)."""
    root = target_dir.resolve()
    real = folder.resolve()
    if real != root and root not in real.parents:
        rel = folder.relative_to(target_dir).as_posix() if folder.is_relative_to(target_dir) else str(folder)
        raise ValueError(f"deploy target folder {rel!r} resolves outside the target workspace "
                         f"(a symlink or junction): {real}")


def target_file(target_dir: Path, target_rel: str) -> Path:
    """target_dir / target_rel, refused (ValueError) when it lands outside the target workspace:
    spelled to escape (check_target_inside) or reached through a linked folder
    (check_folder_inside). Only the folders are resolved: a write replaces the file itself
    (os.replace) and a delete unlinks it, neither follows it."""
    check_target_inside(target_rel)
    path = target_dir / target_rel
    check_folder_inside(target_dir, path.parent)
    return path


def normalize_target(target_rel: str) -> str:
    """Canonical spelling of a target path: './x/', 'x//', 'x/./' all become 'x', and the
    workspace root becomes ''. Plan, prune and the ledger guard compare paths as strings, so
    every target must be spelled one way (df-886.10). Check for escapes BEFORE normalizing."""
    norm = posixpath.normpath(target_rel)
    return "" if norm == "." else norm


def resolve_pairs(source_dir: Path, pipeline: PipelineModel) -> list[tuple[str, str]]:
    """(source_relpath, target_relpath) for every source FILE matched by a pair rule.

    target_relpath = rule.target + (source path relative to the rule's non-wildcard base),
    so the top folder can be renamed while sub-structure is preserved. Targets that escape the
    target workspace (absolute or containing '..') are rejected at plan time. Symlinks are skipped.
    Every rule's target is checked up front, including rules that match no source file: prune
    walks every rule's target, so an unchecked one would delete outside the target (df-886.11).
    """
    for rule in pipeline.pairs:
        check_target_inside(rule.target)
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rule in pipeline.pairs:
        base = source_base(rule.source)
        pattern = rule.source + "/*" if rule.source.endswith("**") else rule.source
        for p in sorted(source_dir.glob(pattern)):
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(source_dir).as_posix()
            tail = rel[len(base):].lstrip("/") if base else rel
            # For exact-file sources (no wildcard), base == rel so tail is empty;
            # fall back to the filename so it is placed inside the target dir.
            if not tail:
                tail = PurePosixPath(rel).name
            target_rel = normalize_target(posixpath.join(rule.target, tail))
            if _escapes(target_rel):   # the rule target was checked; a glob result can't escape
                raise ValueError(f"deploy pair target escapes target workspace: {target_rel!r}")
            # A file NAME Windows can't hold is not fatal here: build_plan skips it (df-886.20).
            key = (rel, target_rel)
            if key in seen:
                continue
            seen.add(key)
            out.append((rel, target_rel))
    return out
