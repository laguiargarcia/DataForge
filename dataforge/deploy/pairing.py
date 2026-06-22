import posixpath
from pathlib import Path, PurePosixPath
import yaml
from .models import PipelineModel


def load_pipeline(workspace_dir: Path, name: str) -> PipelineModel:
    path = workspace_dir / "deploy" / f"{name}.yaml"
    with open(path) as f:
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


def resolve_pairs(source_dir: Path, pipeline: PipelineModel) -> list[tuple[str, str]]:
    """(source_relpath, target_relpath) for every source FILE matched by a pair rule.

    target_relpath = rule.target + (source path relative to the rule's non-wildcard base),
    so the top folder can be renamed while sub-structure is preserved. Targets that escape the
    target workspace (absolute or containing '..') are rejected at plan time. Symlinks are skipped.
    """
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
            target_rel = posixpath.join(rule.target.rstrip("/"), tail)
            if posixpath.isabs(target_rel) or ".." in PurePosixPath(target_rel).parts:
                raise ValueError(f"deploy pair target escapes target workspace: {target_rel!r}")
            key = (rel, target_rel)
            if key in seen:
                continue
            seen.add(key)
            out.append((rel, target_rel))
    return out
