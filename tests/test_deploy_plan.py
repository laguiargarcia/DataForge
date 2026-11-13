from pathlib import Path
from dataforge.deploy.models import Action, Kind
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan, kind_for


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


def _item(plan, target_path: str):
    """The one plan item for target_path (StopIteration if the plan has none)."""
    return next(i for i in plan.items if i.target_path == target_path)


def test_kind_for_by_prefix():
    assert kind_for("scripts/x.py") == Kind.script
    assert kind_for("jobs/j/job.yaml") == Kind.job
    assert kind_for("config/a.yaml") == Kind.config
    assert kind_for("triggers/daily.yaml") == Kind.trigger
    assert kind_for("readme.md") == Kind.other
    assert kind_for("apps/financas/dist/index.html") == Kind.app


def test_add_new_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    plan = _build(src, tgt)
    a = _item(plan, "scripts/a.py")
    assert a.action == Action.add
    assert a.before_sha is None


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
    a = _item(plan, "scripts/a.py")
    assert a.action == Action.modify
    assert a.before_sha != a.after_sha


# --- LAW #3: .deployignore is absolute ---
def test_ignored_path_is_skipped(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    _mk(src, "deploy/.deployignore", "scripts/a.py\n")
    plan = _build(src, tgt)
    a = _item(plan, "scripts/a.py")
    assert a.action == Action.skip_ignored


# --- LAW #2': a trigger is updated in place; the deploy never changes its on/off status ---
def test_active_target_trigger_is_updated_not_protected(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    plan = _build(src, tgt)
    t = _item(plan, "triggers/daily.yaml")
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
    d = _item(plan, "scripts/old.py")
    assert d.action == Action.delete
    assert d.source_path is None


def test_prune_leaves_the_temp_file_of_a_killed_apply_alone(tmp_path):
    # df-ahua.5.5: '.a.py.deploytmp' is what a deploy killed before its os.replace leaves. It is
    # not a definition, so no DELETE for it; the write it belonged to replaces it. A file merely
    # ending in .deploytmp (no leading dot) is an ordinary file and is pruned.
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/.a.py.deploytmp", "half")
    _mk(tgt, "scripts/sub/.b.py.deploytmp", "half")
    _mk(tgt, "scripts/notes.deploytmp", "mine\n")
    _mk(tgt, "scripts/.hidden.py", "old\n")
    p = _pipeline_prune(src, prune=True)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    assert sorted(i.target_path for i in plan.items if i.action == Action.delete) == [
        "scripts/.hidden.py", "scripts/notes.deploytmp"]


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
    extra = _item(plan, "triggers/extra.yaml")
    assert extra.action == Action.protected  # must NOT be delete


def test_renamed_trigger_target_is_updated(tmp_path):  # kind from source
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "schedules/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: true\njobs: []\n")
    _mk(src, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'schedules/'}\n")
    p = load_pipeline(src, "p")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    t = _item(plan, "schedules/daily.yaml")
    assert t.action == Action.modify   # updated, status preserved by apply
    assert t.kind == Kind.trigger


def test_malformed_target_trigger_does_not_crash(tmp_path):  # HIGH — robustness
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "- this\n- is\n- a list\n")  # not a mapping
    _mk(src, "deploy/p.yaml",
        "target: Prod\npairs:\n  - {source: 'triggers/*.yaml', target: 'triggers/'}\n")
    p = load_pipeline(src, "p")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))  # must not raise
    assert any(i.target_path == "triggers/daily.yaml" for i in plan.items)


_PLAN_PROBE = r'''
import json, sys, warnings
from pathlib import Path
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan
src, tgt = Path(sys.argv[1], "Dev"), Path(sys.argv[1], "Prod")
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    p = load_pipeline(src, "to-prod")
    plan = build_plan(src, tgt, p, "to-prod", DeployIgnore.load(src), resolve_pairs(src, p))
print(json.dumps([i.model_dump(mode="json") for i in plan.items]))
'''


def test_plan_does_not_depend_on_the_machine_locale(tmp_path):
    # Workspace files are UTF-8 whatever the locale (df-nfbh). The same accented triggers must
    # give the same plan here and under the C locale (ASCII), where a locale read crashes.
    import json
    import os
    import subprocess
    import sys
    import dataforge
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    trigger = "name: relatório {}\ncron: '{}'\nenabled: {}\njobs: []\n"
    for ws, rel, text in [
        (src, "triggers/daily.yaml", trigger.format("diário", "0 9 * * *", "true")),
        (tgt, "triggers/daily.yaml", trigger.format("diário", "0 9 * * *", "false")),   # in sync
        (src, "triggers/weekly.yaml", trigger.format("semanal", "0 9 * * 1", "true")),
        (tgt, "triggers/weekly.yaml", trigger.format("semanal", "0 5 * * 1", "true")),  # modified
    ]:
        _mk(ws, rel, "").write_bytes(text.encode("utf-8"))
    _pipeline(src)
    here = [i.model_dump(mode="json") for i in _build(src, tgt).items]
    assert [(i["target_path"], i["action"]) for i in here] == [("triggers/weekly.yaml", "MODIFY")]
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
           "PYTHONPATH": str(Path(dataforge.__file__).parent.parent)}
    proc = subprocess.run([sys.executable, "-X", "utf8=0", "-c", _PLAN_PROBE, str(tmp_path)],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    assert json.loads(proc.stdout) == here


def test_app_pair_plans_sources_and_bundle_but_not_build_artifacts(tmp_path):
    # df-ve7: an apps/** pair promotes the app definition, sources and compiled dist/ (binary
    # fonts included) as Kind.app; node_modules/ and test-results/ never travel (default ignore).
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "deploy/to-prod.yaml",
        "target: Prod\npairs:\n  - {source: 'apps/**', target: 'apps/'}\n")
    _mk(src, "apps/financas/app.yaml", "name: financas\n")
    _mk(src, "apps/financas/dist/index.html", "<html></html>")
    font = src / "apps/financas/dist/assets/f.woff2"
    font.parent.mkdir(parents=True)
    font.write_bytes(bytes(range(256)))
    _mk(src, "apps/financas/node_modules/react/index.js", "x")
    _mk(src, "apps/financas/test-results/.last-run.json", "{}")
    p = load_pipeline(src, "to-prod")
    plan = build_plan(src, tgt, p, "to-prod", DeployIgnore.load(src), resolve_pairs(src, p))
    by_target = {i.target_path: i for i in plan.items}
    for rel in ("apps/financas/app.yaml", "apps/financas/dist/index.html",
                "apps/financas/dist/assets/f.woff2"):
        assert by_target[rel].action == Action.add
        assert by_target[rel].kind == Kind.app
    assert by_target["apps/financas/node_modules/react/index.js"].action == Action.skip_ignored
    assert by_target["apps/financas/test-results/.last-run.json"].action == Action.skip_ignored


# --- df-886.13: a target path that exists but isn't a file is reported, not a crash ---

def test_target_path_that_is_a_directory_is_protected(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    _mk(src, "scripts/b.py", "print(2)\n")
    (tgt / "scripts" / "a.py").mkdir(parents=True)
    plan = _build(src, tgt)   # used to raise IsADirectoryError
    a = _item(plan, "scripts/a.py")
    assert a.action == Action.protected
    assert a.reason == "target has a folder where the source has a file"
    assert a.source_path == "scripts/a.py"
    assert a not in plan.writable()
    assert _item(plan, "scripts/b.py").action == Action.add   # one conflict doesn't stop the plan


def test_target_folder_that_is_a_file_is_protected(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/sub/a.py", "print(1)\n")
    _mk(tgt, "scripts/sub", "a file where the source has a folder\n")
    plan = _build(src, tgt)   # used to plan an ADD whose apply fails in mkdir mid-batch
    a = _item(plan, "scripts/sub/a.py")
    assert a.action == Action.protected
    assert a.reason == "target has a file at 'scripts/sub' where the source has a folder"
    assert a not in plan.writable()


def test_outer_target_folder_that_is_a_file_is_named(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/sub/a.py", "print(1)\n")
    _mk(tgt, "scripts", "a file where the source has the scripts folder\n")
    a = _item(_build(src, tgt), "scripts/sub/a.py")
    assert a.action == Action.protected
    assert a.reason == "target has a file at 'scripts' where the source has a folder"


# --- df-94s: .deployignore is checked against BOTH the source and the target path ---

def _renamed(src: Path, rule: str, ignore: str, prune: bool = False):
    _mk(src, "deploy/p.yaml", f"target: Prod\nprune: {str(prune).lower()}\npairs:\n  - {rule}\n")
    _mk(src, "deploy/.deployignore", ignore)
    return load_pipeline(src, "p")


def test_ignore_matches_the_target_path_on_add(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    p = _renamed(src, "{source: 'scripts/**', target: 'secrets/'}", "secrets/\n")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    a = _item(plan, "secrets/a.py")
    assert a.action == Action.skip_ignored


def test_renamed_pair_outside_triggers_keeps_the_source_kind(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    p = _renamed(src, "{source: 'scripts/**', target: 'code/'}", "")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    assert _item(plan, "code/a.py").kind == Kind.script


def test_ignore_matches_the_source_path_on_prune(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")          # keeps the source set non-empty
    _mk(tgt, "code/keep.db", "prod data\n")
    p = _renamed(src, "{source: 'scripts/**', target: 'code/'}", "scripts/keep.db\n", prune=True)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    assert all(i.target_path != "code/keep.db" for i in plan.items)   # was DELETE


def test_ignore_on_prune_still_deletes_what_no_pattern_names(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "print(1)\n")
    _mk(tgt, "code/old.py", "gone\n")
    p = _renamed(src, "{source: 'scripts/**', target: 'code/'}", "scripts/keep.db\n", prune=True)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    old = _item(plan, "code/old.py")
    assert old.action == Action.delete


# --- df-7pl: a file is a trigger when EITHER side of its pair is under triggers/ ---

def test_source_outside_triggers_to_a_live_trigger_keeps_its_status(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "config/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: false\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    p = _renamed(src, "{source: 'config/*.yaml', target: 'triggers/'}", "")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    # Only 'enabled' differs and the target is a live trigger: law #1 makes it a no-op.
    assert all(i.target_path != "triggers/daily.yaml" for i in plan.items)


def test_source_outside_triggers_is_planned_as_a_trigger(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "config/daily.yaml", "name: daily\ncron: '0 5 * * *'\nenabled: false\njobs: []\n")
    _mk(tgt, "triggers/daily.yaml", "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    p = _renamed(src, "{source: 'config/*.yaml', target: 'triggers/'}", "")
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    t = _item(plan, "triggers/daily.yaml")
    assert t.action == Action.modify   # apply then keeps enabled: true
    assert t.kind == Kind.trigger


def test_prune_protects_an_active_trigger_under_a_renamed_target(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "triggers/other.yaml", "name: other\ncron: '0 9 * * *'\njobs: []\n")
    _mk(tgt, "schedules/x.yaml", "name: x\ncron: '0 9 * * *'\nenabled: true\njobs: []\n")
    p = _renamed(src, "{source: 'triggers/*.yaml', target: 'schedules/'}", "", prune=True)
    plan = build_plan(src, tgt, p, "p", DeployIgnore.load(src), resolve_pairs(src, p))
    x = _item(plan, "schedules/x.yaml")
    assert x.action == Action.protected   # was DELETE, kind other
    assert x.kind == Kind.trigger
