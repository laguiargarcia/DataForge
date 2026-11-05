"""Laws of the deploy planner, checked over generated workspaces.

Each example builds its own source and target under a temporary directory created inside the
example, because a test-scoped fixture would be shared between examples. A test marked
xfail(strict=True) records a real bug: its reason is the beads id, and the fix must remove the mark.
"""
import re
import string
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.models import Action, Kind, PairRule, PipelineModel
from dataforge.deploy.pairing import resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.models import TriggerModel

# database=None: a plain pytest run, outside the deterministic profile, does not replay failures
# stored by an earlier run.
LAW = settings(max_examples=25, deadline=None, database=None,
               suppress_health_check=[HealthCheck.too_slow])

BASES = {"config": Kind.config, "docs": Kind.other, "jobs": Kind.job,
         "scripts": Kind.script, "triggers": Kind.trigger}
IGNORE_RULES = "*.keep\nvault_/\n"
SHA_FORM = re.compile(r"sha256:[0-9a-f]{64}")
ABSENT = "absent"

SEGMENT = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=6)
TAIL = st.builds(lambda dirs, name: "/".join([*dirs, name]),
                 st.lists(SEGMENT, max_size=2), SEGMENT)
BLOB = st.binary(max_size=24)
DEFINITION = st.fixed_dictionaries(
    {"name": SEGMENT, "cron": st.sampled_from(["0 9 * * *", "0 5 * * 1"])},
    optional={"jobs": st.just([]), "note": st.text(alphabet="aé ç", max_size=4)},
)


@contextmanager
def workspaces():
    with tempfile.TemporaryDirectory() as root:
        source, target = Path(root, "Dev"), Path(root, "Prod")
        source.mkdir()
        target.mkdir()
        yield source, target


def plant(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def plan_for(source: Path, target: Path, rules: list[tuple[str, str]], prune: bool = True):
    pipeline = PipelineModel(target="Prod", prune=prune,
                             pairs=[PairRule(source=s, target=t) for s, t in rules])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # the empty-source warning has its own law
        return build_plan(source, target, pipeline, "p", DeployIgnore.load(source),
                          resolve_pairs(source, pipeline))


def same_folder(*bases: str) -> list[tuple[str, str]]:
    return [(f"{base}/**", f"{base}/") for base in bases]


def trigger_bytes(definition: dict, enabled=ABSENT) -> bytes:
    doc = dict(definition)
    if enabled != ABSENT:
        doc["enabled"] = enabled
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).encode()


def actions(plan) -> dict[str, Action]:
    return {item.target_path: item.action for item in plan.items}


# --- The file-by-file oracle: the expected plan comes from how each file was laid out ---

STATES = ["new", "same", "changed", "ignored", "ignored_target_only", "target_only",
          "active", "inactive", "malformed"]


@st.composite
def laid_out_workspaces(draw):
    # triggers are always in play: they have the most states, and a free draw rarely reached them
    others = sorted(set(BASES) - {"triggers"})
    bases = ["triggers", *draw(st.lists(st.sampled_from(others), max_size=2, unique=True))]
    spots = draw(st.lists(
        st.tuples(st.sampled_from(bases), TAIL, st.sampled_from(STATES), BLOB, DEFINITION),
        min_size=1, max_size=8, unique_by=lambda spot: spot[:2],
    ))
    return bases, spots, draw(st.booleans()), draw(st.lists(SEGMENT, max_size=2, unique=True))


# Every state in both families of file, so each run sees all of them whatever gets generated.
EVERY_STATE = (
    ["scripts", "triggers"],
    [(base, f"f{position}", state, b"blob", {"name": "n", "cron": "0 9 * * *"})
     for base in ("scripts", "triggers") for position, state in enumerate(STATES)],
    True,
    ["unpaired"],
)


def lay_out(bases, spots, prune, unpaired):
    """Return (source files, target files, expected {target path: (action, source path)})."""
    source, target, expected = {}, {f"data/{name}.txt": b"x" for name in unpaired}, {}
    for base, tail, state, blob, definition in spots:
        is_trigger = base == "triggers"
        rel = f"{base}/{tail}.yaml"
        if state.startswith("ignored"):
            rel = f"{base}/{tail}.keep" if len(tail) % 2 else f"{base}/vault_/{tail}.txt"
        ours = trigger_bytes({**definition, "rev": 1}, True) if is_trigger else blob + b"s"
        theirs = trigger_bytes({**definition, "rev": 2}, True) if is_trigger else blob + b"t"
        if state == "new":
            source[rel] = ours
            expected[rel] = (Action.add, rel)
        elif state == "same":
            source[rel] = ours
            # a trigger's on/off status is not part of its definition
            target[rel] = trigger_bytes({**definition, "rev": 1}, False) if is_trigger else ours
        elif state == "changed":
            source[rel], target[rel] = ours, theirs
            expected[rel] = (Action.modify, rel)
        elif state == "ignored":
            source[rel], target[rel] = ours, theirs
            expected[rel] = (Action.skip_ignored, rel)
        elif state == "ignored_target_only":
            target[rel] = theirs
        elif not is_trigger or state in ("target_only", "inactive"):
            target[rel] = trigger_bytes(definition, False) if is_trigger else blob
            expected[rel] = (Action.delete, None)
        elif state == "active":
            target[rel] = trigger_bytes(definition, True)
            expected[rel] = (Action.protected, None)
        else:  # malformed: not a mapping, so the engine cannot be running it
            target[rel] = b"- not\n- a mapping\n"
            expected[rel] = (Action.delete, None)
    if not prune or not source:
        expected = {rel: e for rel, e in expected.items()
                    if e[0] not in (Action.delete, Action.protected)}
    return source, target, expected


@LAW
@given(laid_out_workspaces())
@example(EVERY_STATE)
def test_every_file_gets_the_action_its_state_demands(scenario):
    bases, spots, prune, unpaired = scenario
    source_files, target_files, expected = lay_out(bases, spots, prune, unpaired)
    with workspaces() as (source, target):
        plant(source, source_files | {"deploy/.deployignore": IGNORE_RULES.encode()})
        plant(target, target_files)
        plan = plan_for(source, target, same_folder(*bases), prune)
        blob_id = Ledger(source.parent / "blobs").write_blob

        assert len(plan.items) == len(expected), "a target file is listed at most once"
        assert {i.target_path: (i.action, i.source_path) for i in plan.items} == expected
        for item in plan.items:
            assert item.kind == BASES[item.target_path.split("/")[0]]
            assert item.reason, "the deploy panel shows every item's reason"
            for sha in (item.before_sha, item.after_sha):
                assert sha is None or SHA_FORM.fullmatch(sha)
            hashes_content = item.kind != Kind.trigger
            if item.action == Action.add:
                assert item.before_sha is None and item.after_sha
                if hashes_content:
                    assert item.after_sha == blob_id(source_files[item.target_path])
            elif item.action == Action.modify:
                assert item.before_sha and item.after_sha and item.before_sha != item.after_sha
                if hashes_content:
                    assert item.before_sha == blob_id(target_files[item.target_path])
                    assert item.after_sha == blob_id(source_files[item.target_path])
            elif item.action == Action.delete:
                assert item.before_sha == blob_id(target_files[item.target_path])
                assert item.after_sha is None


# --- Law #4: the ledger belongs to the engine ---

@LAW
@given(st.lists(SEGMENT, min_size=1, max_size=3, unique=True), SEGMENT, st.booleans())
def test_no_item_ever_touches_the_ledger(names, incoming, ledger_is_a_file):
    with workspaces() as (source, target):
        if ledger_is_a_file:
            into_ledger = ("exact/*", "deploy/")
            plant(source, {"exact/.ledger": b"from source"})
            plant(target, {"deploy/.ledger": b"state"})
        else:
            into_ledger = ("incoming/**", "deploy/.ledger/")
            plant(source, {f"incoming/{incoming}.txt": b"from source"})
            plant(target, {f"deploy/.ledger/{name}.jsonl": b"state" for name in names})
        plant(source, {"scripts/kept_.txt": b"s"})
        neighbours = {f"deploy/{name}.txt" for name in names} | {"deploy/.ledgerx"}
        plant(target, dict.fromkeys(neighbours, b"n"))

        plan = plan_for(source, target, [into_ledger, ("scripts/**", "deploy/")])

    listed = actions(plan)
    assert not [path for path in listed
                if path == "deploy/.ledger" or path.startswith("deploy/.ledger/")]
    # the guard stops at the ledger: its neighbours are still planned
    assert listed["deploy/kept_.txt"] == Action.add
    assert all(listed[path] == Action.delete for path in neighbours)


# --- df-gl6: an in-sync file is not a change, and not absent either ---

@LAW
@given(st.dictionaries(TAIL, BLOB, min_size=1, max_size=4),
       st.lists(SEGMENT, min_size=1, max_size=2, unique=True), st.booleans())
def test_in_sync_file_is_never_deleted_or_listed(files, extras, prune):
    in_sync = {f"scripts/{tail}.py": blob for tail, blob in files.items()}
    only_target = {f"scripts/{name}.extra": b"x" for name in extras}
    with workspaces() as (source, target):
        plant(source, in_sync)
        plant(target, in_sync | only_target)
        plan = plan_for(source, target, same_folder("scripts"), prune)
    listed = actions(plan)
    assert not set(in_sync) & set(listed)
    assert set(listed) == (set(only_target) if prune else set())


# --- Law #3: .deployignore is absolute ---

@LAW
@given(st.lists(TAIL, min_size=1, max_size=3, unique=True),
       st.lists(SEGMENT, min_size=1, max_size=2, unique=True))
def test_ignored_path_is_never_deleted(tails, deletable):
    ignored = [f"scripts/{tail}.keep" for tail in tails] + [f"scripts/vault_/{tails[0]}.txt"]
    others = {f"scripts/{name}.txt": b"x" for name in deletable}
    with workspaces() as (source, target):
        plant(source, {"deploy/.deployignore": IGNORE_RULES.encode(), "scripts/a.py": b"a"})
        plant(target, dict.fromkeys(ignored, b"precious") | others)
        plan = plan_for(source, target, same_folder("scripts"))
    listed = actions(plan)
    assert not set(ignored) & set(listed)
    assert all(listed[path] == Action.delete for path in others)  # not vacuous


# --- Law #2: an active trigger in the target is never deleted ---

@LAW
@given(st.lists(st.tuples(SEGMENT, DEFINITION), min_size=1, max_size=3, unique_by=lambda t: t[0]),
       st.sampled_from([True, 1, "yes please"]))
def test_active_trigger_is_protected_never_deleted(triggers, truthy):
    active = {f"triggers/{name}.yaml": trigger_bytes(d, truthy) for name, d in triggers}
    inactive = {f"triggers/{name}.off.yaml": trigger_bytes(d, False) for name, d in triggers}
    with workspaces() as (source, target):
        plant(source, {"triggers/paired.yaml": trigger_bytes({"name": "paired", "cron": "x"})})
        plant(target, active | inactive)
        plan = plan_for(source, target, same_folder("triggers"))
    by_path = {item.target_path: item for item in plan.items}
    for path in active:
        assert by_path[path].action == Action.protected
        assert by_path[path].source_path is None
    assert all(by_path[path].action == Action.delete for path in inactive)  # the guard is selective


# --- prune is opt-in, and an empty source is never "delete everything" ---

@LAW
@given(st.dictionaries(TAIL, BLOB, max_size=3), st.dictionaries(TAIL, BLOB, min_size=1, max_size=3))
def test_prune_off_produces_no_delete_or_protected(in_source, in_target):
    with workspaces() as (source, target):
        plant(source, {f"scripts/s{tail}.py": blob for tail, blob in in_source.items()})
        plant(target, {f"scripts/t{tail}.py": blob for tail, blob in in_target.items()} |
              {f"triggers/t{tail}.yaml": trigger_bytes({"name": "t"}, True) for tail in in_target})
        plan = plan_for(source, target, same_folder("scripts", "triggers"), prune=False)
    assert not {Action.delete, Action.protected} & {item.action for item in plan.items}


@LAW
@given(st.dictionaries(TAIL, BLOB, min_size=1, max_size=4))
def test_empty_pairs_produce_no_delete_and_say_so(in_target):
    with workspaces() as (source, target):
        plant(target, {f"scripts/{tail}.py": blob for tail, blob in in_target.items()})
        pipeline = PipelineModel(target="Prod", prune=True,
                                 pairs=[PairRule(source="scripts/**", target="scripts/")])
        with pytest.warns(UserWarning, match="(?i)prune"):
            plan = build_plan(source, target, pipeline, "p", DeployIgnore.load(source), [])
    assert plan.items == []


# --- Law #1: 'enabled' is status, not definition ---

@LAW
@given(DEFINITION, st.sampled_from([ABSENT, True, False]), st.sampled_from([ABSENT, True, False]))
def test_changing_only_enabled_of_a_trigger_is_no_change(definition, ours, theirs):
    with workspaces() as (source, target):
        plant(source, {"triggers/t.yaml": trigger_bytes(definition, ours)})
        plant(target, {"triggers/t.yaml": trigger_bytes(definition, theirs)})
        assert plan_for(source, target, same_folder("triggers")).items == []


@LAW
@given(DEFINITION, st.booleans())
def test_enabled_is_part_of_the_content_of_anything_but_a_trigger(definition, ours):
    with workspaces() as (source, target):
        plant(source, {"config/t.yaml": trigger_bytes(definition, ours)})
        plant(target, {"config/t.yaml": trigger_bytes(definition, not ours)})
        plan = plan_for(source, target, same_folder("config"))
    assert actions(plan) == {"config/t.yaml": Action.modify}


@LAW
@given(st.dictionaries(st.text(string.ascii_lowercase, min_size=1, max_size=4), SEGMENT,
                       min_size=2, max_size=5)
       .flatmap(lambda d: st.tuples(st.just(d), st.permutations(sorted(d)))))
def test_key_order_of_a_trigger_is_no_change(definition_and_order):
    definition, order = definition_and_order
    reordered = {key: definition[key] for key in order}
    with workspaces() as (source, target):
        plant(source, {"triggers/t.yaml": trigger_bytes(dict(sorted(definition.items())))})
        plant(target, {"triggers/t.yaml": trigger_bytes(reordered)})
        assert plan_for(source, target, same_folder("triggers")).items == []


@LAW
@given(st.sampled_from([b"- a\n- list\n", b"just text\n", b"key: [unclosed\n"]),
       st.sampled_from([b"- other\n", b"plain\n"]))
def test_unusable_trigger_is_compared_by_its_bytes(ours, theirs):
    with workspaces() as (source, target):
        plant(source, {"triggers/same.yaml": ours, "triggers/diff.yaml": ours})
        plant(target, {"triggers/same.yaml": ours, "triggers/diff.yaml": theirs})
        plan = plan_for(source, target, same_folder("triggers"))
    assert actions(plan) == {"triggers/diff.yaml": Action.modify}


# --- Planning is a pure read ---

@LAW
@given(laid_out_workspaces())
@example(EVERY_STATE)
def test_planning_twice_gives_equal_plans_and_writes_nothing(scenario):
    bases, spots, prune, unpaired = scenario
    source_files, target_files, _ = lay_out(bases, spots, prune, unpaired)
    with workspaces() as (source, target):
        plant(source, source_files | {"deploy/.deployignore": IGNORE_RULES.encode()})
        plant(target, target_files)
        snapshot = sorted((str(p), p.read_bytes() if p.is_file() else None)
                          for root in (source, target) for p in root.rglob("*"))
        first = plan_for(source, target, same_folder(*bases), prune)
        second = plan_for(source, target, same_folder(*bases), prune)
        assert first == second
        assert snapshot == sorted((str(p), p.read_bytes() if p.is_file() else None)
                                  for root in (source, target) for p in root.rglob("*"))


# --- Which folders prune walks ---

@LAW
@given(st.lists(SEGMENT, min_size=1, max_size=3, unique=True))
def test_a_missing_base_does_not_stop_prune_of_the_others(names):
    extras = {f"scripts/{name}.old": b"x" for name in names}
    with workspaces() as (source, target):
        plant(source, {"scripts/a.py": b"a"})
        plant(target, extras)  # target has no config/, which sorts before scripts/
        plan = plan_for(source, target, same_folder("config", "scripts"))
    assert set(actions(plan)) == set(extras) | {"scripts/a.py"}


@LAW
@given(st.lists(SEGMENT, min_size=1, max_size=3, unique=True))
def test_base_spelled_with_and_without_slash_lists_each_file_once(names):
    extras = {f"scripts/{name}.old": b"x" for name in names}
    with workspaces() as (source, target):
        plant(source, {"a/a.py": b"a", "b/b.py": b"b"})
        plant(target, extras)
        plan = plan_for(source, target, [("a/**", "scripts"), ("b/**", "scripts/")])
    paths = [item.target_path for item in plan.items]
    assert len(paths) == len(set(paths))
    assert set(extras) <= set(paths)


def test_only_the_slash_is_stripped_from_a_base():
    with workspaces() as (source, target):
        plant(source, {"a/a.py": b"a"})
        plant(target, {"boxX/old.py": b"x"})
        plan = plan_for(source, target, [("a/**", "boxX/")])
    assert actions(plan)["boxX/old.py"] == Action.delete


def test_symlink_in_target_is_never_pruned():
    with workspaces() as (source, target):
        plant(source, {"scripts/a.py": b"a"})
        plant(target, {"elsewhere/real.txt": b"real", "scripts/z.py": b"z"})
        try:
            (target / "scripts" / "link.py").symlink_to(target / "elsewhere" / "real.txt")
        except OSError:
            pytest.skip("this system cannot create symlinks")
        plan = plan_for(source, target, same_folder("scripts"))
    assert actions(plan) == {"scripts/a.py": Action.add, "scripts/z.py": Action.delete}


# --- Real bugs. Each mark comes off when its beads issue is fixed ---

def test_engine_treats_a_trigger_without_enabled_as_active():
    """The premise of df-886.8: this is why the absent key means active."""
    assert TriggerModel(name="x", cron="0 5 * * *").enabled is True


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="df-886.8")
def test_trigger_without_enabled_key_is_protected():
    with workspaces() as (source, target):
        plant(source, {"triggers/a.yaml": b"name: a\ncron: '0 9 * * *'\n"})
        plant(target, {"triggers/x.yaml": b"name: x\ncron: '0 5 * * *'\n"})
        plan = plan_for(source, target, same_folder("triggers"))
    assert actions(plan)["triggers/x.yaml"] == Action.protected


DIRTY_TRIGGERS = [b"\xff\xfe", b"d: 2001-13-45\n"]


@pytest.mark.xfail(strict=True, raises=ValueError, reason="df-886.9")
@pytest.mark.parametrize("dirty", DIRTY_TRIGGERS, ids=["not-utf8", "invalid-date"])
@pytest.mark.parametrize("where", ["source", "target", "prune-candidate"])
def test_dirty_trigger_file_does_not_crash_the_planner(where, dirty):
    good = b"name: a\ncron: '0 9 * * *'\n"
    files = {"source": ({"triggers/a.yaml": dirty}, {}),
             "target": ({"triggers/a.yaml": good}, {"triggers/a.yaml": dirty}),
             "prune-candidate": ({"triggers/a.yaml": good}, {"triggers/x.yaml": dirty})}[where]
    with workspaces() as (source, target):
        plant(source, files[0])
        plant(target, files[1])
        plan_for(source, target, same_folder("triggers"))


@pytest.mark.xfail(strict=True, raises=ValueError, reason="df-886.9")
@settings(LAW, report_multiple_bugs=False)
@given(st.binary(max_size=32))
@example(b"\xff\xfe")
def test_any_bytes_in_a_trigger_file_leave_the_planner_standing(content):
    with workspaces() as (source, target):
        plant(source, {"triggers/a.yaml": content})
        plant(target, {"triggers/a.yaml": b"name: a\n", "triggers/x.yaml": content})
        plan_for(source, target, same_folder("triggers"))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="df-886.10")
@pytest.mark.parametrize("spelling,on_disk", [
    ("./scripts/", "scripts/a.py"),
    ("scripts/./", "scripts/a.py"),
    ("x//y/", "x/y/a.py"),
])
def test_in_sync_file_survives_any_spelling_of_the_target(spelling, on_disk):
    with workspaces() as (source, target):
        plant(source, {"scripts/a.py": b"same"})
        plant(target, {on_disk: b"same"})
        plan = plan_for(source, target, [("scripts/**", spelling)])
    assert on_disk not in {i.target_path for i in plan.items if i.action == Action.delete}


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="df-886.10")
@pytest.mark.parametrize("spelling", ["./deploy/.ledger/", "deploy//.ledger/", "deploy/./.ledger/"])
def test_ledger_guard_sees_any_spelling_of_the_target(spelling):
    with workspaces() as (source, target):
        plant(source, {"scripts/a.py": b"new"})
        plan = plan_for(source, target, [("scripts/**", spelling)], prune=False)
    assert plan.items == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="df-886.11")
@pytest.mark.parametrize("escape", ["../Other/", "sub/../../Other/"])
def test_prune_never_plans_a_delete_outside_the_target(escape):
    with workspaces() as (source, target):
        plant(source, {"scripts/a.py": b"a"})
        plant(target.parent / "Other", {"victim.py": b"v"})
        plant(target, {"sub/keep.txt": b"k"})
        try:
            plan = plan_for(source, target, [("scripts/**", "scripts/"), ("nomatch/**", escape)])
        except ValueError:
            return  # refusing the pipeline keeps the law too, as resolve_pairs does for a matched rule
    assert not [i.target_path for i in plan.items
                if i.action == Action.delete and ".." in i.target_path.split("/")]
