import pytest
from pathlib import Path
from dataforge.deploy.models import PipelineModel
from dataforge.deploy.pairing import load_pipeline, resolve_pairs, source_base, source_equivalents


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_source_base_strips_at_first_wildcard():
    assert source_base("jobs/**") == "jobs"
    assert source_base("config/*.yaml") == "config"
    assert source_base("scripts/sub/x.py") == "scripts/sub/x.py"


def test_load_pipeline(tmp_path):
    _mk(tmp_path, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    p = load_pipeline(tmp_path, "to-prod")
    assert p.target == "Prod"
    assert p.pairs[0].source == "scripts/**"


def test_resolve_pairs_maps_globs_to_target(tmp_path):
    _mk(tmp_path, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n"
        "  - {source: 'jobs/**', target: 'jobs/'}\n"
        "  - {source: 'config/*.yaml', target: 'config/'}\n")
    _mk(tmp_path, "jobs/finance_daily/job.yaml", "name: finance_daily\n")
    _mk(tmp_path, "config/accounts.yaml", "x: 1\n")
    _mk(tmp_path, "config/notes.txt", "ignore me")  # not matched by *.yaml
    p = load_pipeline(tmp_path, "to-prod")
    pairs = resolve_pairs(tmp_path, p)
    assert ("jobs/finance_daily/job.yaml", "jobs/finance_daily/job.yaml") in pairs
    assert ("config/accounts.yaml", "config/accounts.yaml") in pairs
    assert all(src != "config/notes.txt" for src, _ in pairs)


def test_resolve_pairs_can_rename_top_dir(tmp_path):
    _mk(tmp_path, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'src/'}\n")
    _mk(tmp_path, "scripts/a.py", "pass\n")
    p = load_pipeline(tmp_path, "p")
    assert ("scripts/a.py", "src/a.py") in resolve_pairs(tmp_path, p)


def test_resolve_pairs_rejects_parent_escape(tmp_path):
    _mk(tmp_path, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: '../evil/'}\n")
    _mk(tmp_path, "scripts/a.py", "x\n")
    p = load_pipeline(tmp_path, "p")
    with pytest.raises(ValueError):
        resolve_pairs(tmp_path, p)


def test_resolve_pairs_rejects_absolute_target(tmp_path):
    _mk(tmp_path, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: '/etc/'}\n")
    _mk(tmp_path, "scripts/a.py", "x\n")
    p = load_pipeline(tmp_path, "p")
    with pytest.raises(ValueError):
        resolve_pairs(tmp_path, p)


def test_resolve_pairs_keeps_same_source_to_two_targets(tmp_path):
    _mk(tmp_path, "deploy/p.yaml",
        "target: Prod\npairs:\n"
        "  - {source: 'scripts/a.py', target: 'scripts/'}\n"
        "  - {source: 'scripts/a.py', target: 'backup/'}\n")
    _mk(tmp_path, "scripts/a.py", "x\n")
    p = load_pipeline(tmp_path, "p")
    pairs = resolve_pairs(tmp_path, p)
    assert ("scripts/a.py", "scripts/a.py") in pairs
    assert ("scripts/a.py", "backup/a.py") in pairs


def test_resolve_pairs_skips_symlinks(tmp_path):
    _mk(tmp_path, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    _mk(tmp_path, "scripts/real.py", "x\n")
    outside = _mk(tmp_path, "secret.txt", "secret\n")
    (tmp_path / "scripts" / "link.py").symlink_to(outside)
    p = load_pipeline(tmp_path, "p")
    pairs = resolve_pairs(tmp_path, p)
    assert ("scripts/real.py", "scripts/real.py") in pairs
    assert all(src != "scripts/link.py" for src, _ in pairs)


# --- df-nyf: two different source files must never share one target path ---

def test_two_sources_to_one_target_are_refused(tmp_path):
    _mk(tmp_path, "deploy/p.yaml", "target: Prod\npairs:\n"
        "  - {source: 'scripts/*.py', target: 't/'}\n"
        "  - {source: 'other/*.py', target: 't/'}\n")
    _mk(tmp_path, "scripts/a.py", "one\n")
    _mk(tmp_path, "other/a.py", "two\n")
    with pytest.raises(ValueError, match="t/a.py"):
        resolve_pairs(tmp_path, load_pipeline(tmp_path, "p"))


def test_two_sources_to_one_target_differing_only_in_case_are_refused(tmp_path):
    _mk(tmp_path, "deploy/p.yaml", "target: Prod\npairs:\n"
        "  - {source: 'scripts/*.py', target: 't/'}\n"
        "  - {source: 'other/*.py', target: 'T/'}\n")
    _mk(tmp_path, "scripts/a.py", "one\n")
    _mk(tmp_path, "other/a.py", "two\n")
    with pytest.raises(ValueError, match="(?i)t/a.py"):   # one file on NTFS
        resolve_pairs(tmp_path, load_pipeline(tmp_path, "p"))


def test_one_source_matched_by_two_rules_is_listed_once(tmp_path):
    _mk(tmp_path, "deploy/p.yaml", "target: Prod\npairs:\n"
        "  - {source: 'scripts/**', target: 'scripts/'}\n"
        "  - {source: 'scripts/*.py', target: 'scripts/'}\n")
    _mk(tmp_path, "scripts/a.py", "one\n")
    assert resolve_pairs(tmp_path, load_pipeline(tmp_path, "p")) == [("scripts/a.py", "scripts/a.py")]


def test_source_equivalents_runs_the_pair_rules_backwards():
    p = PipelineModel(target="Prod", pairs=[
        {"source": "scripts/**", "target": "code/"},
        {"source": "config/app.yaml", "target": "cfg/"},
        {"source": "*.md", "target": "."},
    ])
    assert source_equivalents(p, "code/sub/a.py") == ["scripts/sub/a.py", "code/sub/a.py"]
    assert source_equivalents(p, "cfg/app.yaml") == ["config/app.yaml", "cfg/app.yaml"]
    assert source_equivalents(p, "cfg/other.yaml") == ["cfg/other.yaml"]   # exact file: name only
    assert source_equivalents(p, "codex/a.py") == ["codex/a.py"]          # not under 'code/'


def test_resolve_pairs_overlapping_rules_keep_every_file_once(tmp_path):
    """Two rules can reach the same file onto the same target (an exact rule plus a glob over its
    folder). The repeat is dropped, and the rule's other files still pair: a repeat is skipped,
    it does not end the rule."""
    _mk(tmp_path, "deploy/p.yaml",
        "target: Prod\npairs:\n"
        "  - {source: 'jobs/a.yaml', target: 'jobs/'}\n"
        "  - {source: 'jobs/*', target: 'jobs/'}\n")
    _mk(tmp_path, "jobs/a.yaml", "a\n")
    _mk(tmp_path, "jobs/b.yaml", "b\n")
    pairs = resolve_pairs(tmp_path, load_pipeline(tmp_path, "p"))
    assert pairs == [("jobs/a.yaml", "jobs/a.yaml"), ("jobs/b.yaml", "jobs/b.yaml")]


@pytest.mark.parametrize("name", ["Xa.py", "X/inner.py", "XX.py"])
def test_resolve_pairs_keeps_the_path_under_the_base_verbatim(tmp_path, name):
    """target = rule target + the source path below the base, character for character: only the
    separator after the base is dropped, never the start of a name."""
    _mk(tmp_path, "deploy/p.yaml", "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'src/'}\n")
    _mk(tmp_path, f"scripts/{name}", "x\n")
    assert resolve_pairs(tmp_path, load_pipeline(tmp_path, "p")) == [(f"scripts/{name}", f"src/{name}")]
