from pathlib import Path
from dataforge.deploy.models import Action, Kind
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan, kind_for, content_sha


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _pipeline(src: Path):
    _mk(src, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n"
        "  - {source: 'scripts/**', target: 'scripts/'}\n"
        "  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    return load_pipeline(src, "to-prod")


def _build(src: Path, tgt: Path):
    p = _pipeline(src)
    pairs = resolve_pairs(src, p)
    return build_plan(src, tgt, p, "to-prod", DeployIgnore.load(src), pairs)


def test_kind_for_by_prefix():
    assert kind_for("scripts/x.py") == Kind.script
    assert kind_for("jobs/j/job.yaml") == Kind.job
    assert kind_for("config/a.yaml") == Kind.config
    assert kind_for("triggers/daily.yaml") == Kind.trigger
    assert kind_for("readme.md") == Kind.other


def test_add_new_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    plan = _build(src, tgt)
    a = [i for i in plan.items if i.target_path == "scripts/a.py"][0]
    assert a.action == Action.add and a.before_sha is None


def test_identical_file_is_noop(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "same\n")
    _mk(tgt, "scripts/a.py", "same\n")
    plan = _build(src, tgt)
    assert all(i.target_path != "scripts/a.py" for i in plan.items)


def test_modify_changed_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/a.py", "old\n")
    plan = _build(src, tgt)
    a = [i for i in plan.items if i.target_path == "scripts/a.py"][0]
    assert a.action == Action.modify and a.before_sha != a.after_sha


# --- LAW #3: .deployignore is absolute ---
def test_ignored_path_is_skipped(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    _mk(src, "deploy/.deployignore", "scripts/a.py\n")
    plan = _build(src, tgt)
    a = [i for i in plan.items if i.target_path == "scripts/a.py"][0]
    assert a.action == Action.skip_ignored


# --- LAW #2': a trigger is updated in place; the deploy never changes its on/off status ---
def test_active_target_trigger_is_updated_not_protected(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    plan = _build(src, tgt)
    t = [i for i in plan.items if i.target_path == "triggers/daily.yaml"][0]
    assert t.action == Action.modify   # definition differs -> update; apply preserves 'enabled'


# --- LAW #1: never promote active state (enabled excluded from compare) ---
def test_trigger_enabled_flag_excluded_from_compare(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: false\njobs: []\n")
    plan = _build(src, tgt)
    assert all(i.target_path != "triggers/daily.yaml" for i in plan.items)


def _pipeline_prune(src: Path, prune: bool):
    _mk(src, "deploy/p.yaml",
        f"target: Prod\nprune: {str(prune).lower()}\n"
        "pairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    return load_pipeline(src, "p")


def test_prune_off_leaves_extra_target_files(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/old.py", "old\n")  # exists only in target
    p = _pipeline_prune(src, prune=False)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    assert all(i.action != Action.delete for i in plan.items)


def test_prune_on_marks_extra_target_files_for_delete(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/old.py", "old\n")
    p = _pipeline_prune(src, prune=True)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    d = [i for i in plan.items if i.target_path == "scripts/old.py"][0]
    assert d.action == Action.delete and d.source_path is None


# --- df-gl6: prune must compare against the SOURCE set, not the set of CHANGES ---
def test_prune_keeps_in_sync_files_and_deletes_only_source_absent(tmp_path):
    """N source files identical in target + 2 target-only files -> exactly the 2 are deleted.
    Regression for df-gl6: an in-sync file produces no ItemChange, so the prune set must not
    be derived from planned changes (which would mark every up-to-date file as 'absent')."""
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    for name in ("a.py", "b.py", "c.py"):  # N=3, all identical in source & target
        _mk(src, f"scripts/{name}", "same\n")
        _mk(tgt, f"scripts/{name}", "same\n")
    _mk(tgt, "scripts/old1.py", "x\n")  # target-only (genuinely source-absent)
    _mk(tgt, "scripts/old2.py", "y\n")  # target-only (genuinely source-absent)
    p = _pipeline_prune(src, prune=True)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    deletes = {i.target_path for i in plan.items if i.action == Action.delete}
    assert deletes == {"scripts/old1.py", "scripts/old2.py"}


def test_prune_does_not_delete_when_source_set_empty(tmp_path):
    """Empty-source guard: if no source files resolved, prune must NOT delete the whole target
    (empty-source-implies-delete-everything is never the intent)."""
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    # Source has NO scripts; target has files. Pipeline pairs 'scripts/**'.
    _mk(tgt, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/b.py", "b\n")
    p = _pipeline_prune(src, prune=True)
    pairs = resolve_pairs(src, p)
    assert pairs == []  # source enumeration is genuinely empty
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), pairs)
    assert all(i.action != Action.delete for i in plan.items)


def test_prune_skips_ignored_target_files(tmp_path):  # CRITICAL — law #3
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/keep.duckdb", "data\n")  # only in target, and ignored
    _mk(src, "deploy/p.yaml",
        "target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    _mk(src, "deploy/.deployignore", "*.duckdb\n")
    p = load_pipeline(src, "p")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    assert all(i.target_path != "scripts/keep.duckdb" for i in plan.items)


def test_prune_protects_active_target_trigger(tmp_path):  # CRITICAL — law #2
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/a.yaml", "name: a\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/extra.yaml", "name: extra\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(src, "deploy/p.yaml",
        "target: Prod\nprune: true\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    p = load_pipeline(src, "p")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    extra = [i for i in plan.items if i.target_path == "triggers/extra.yaml"][0]
    assert extra.action == Action.protected  # must NOT be delete


def test_renamed_trigger_target_is_updated(tmp_path):  # kind from source
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "schedules/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(src, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'schedules/'}\n")
    p = load_pipeline(src, "p")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    t = [i for i in plan.items if i.target_path == "schedules/daily.yaml"][0]
    assert t.action == Action.modify and t.kind == Kind.trigger   # updated, status preserved by apply


def test_malformed_target_trigger_does_not_crash(tmp_path):  # HIGH — robustness
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "- this\n- is\n- a list\n")  # not a mapping
    _mk(src, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    p = load_pipeline(src, "p")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))  # must not raise
    assert any(i.target_path == "triggers/daily.yaml" for i in plan.items)
