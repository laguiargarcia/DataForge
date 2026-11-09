"""Laws of deploy apply, checked over generated source and target workspaces.

Each example builds its own workspaces in a temporary directory created inside the example,
because a test-scoped fixture would be shared between examples.
"""
import hashlib
import string
import tempfile
import warnings
from pathlib import Path

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from dataforge.deploy.apply import apply_plan
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.models import Action, PairRule, PipelineModel
from dataforge.deploy.pairing import resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.rollback import rollback_last

LAW = settings(max_examples=40, deadline=None, database=None,
               suppress_health_check=[HealthCheck.too_slow])

LEDGER = "deploy/.ledger"
# Folders never contain a dot and files always end in .py, so a file never collides with a folder.
DIR = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=4)
FILE = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=5).map(lambda s: s + ".py")
REL = st.builds(lambda dirs, name: "/".join(["scripts", *dirs, name]), st.lists(DIR, max_size=2), FILE)
# How a path stands between the two workspaces before the deploy.
STATE = st.sampled_from(["source_only", "target_only", "same", "different"])
CASES = st.dictionaries(REL, st.tuples(STATE, st.binary(max_size=16)), min_size=1, max_size=8)


def plant(source: Path, target: Path, cases: dict) -> None:
    for rel, (state, blob) in cases.items():
        if state in ("source_only", "same", "different"):
            write(source / rel, blob)
        if state in ("target_only", "same"):
            write(target / rel, blob)
        if state == "different":
            write(target / rel, blob + b"#old")


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def files(root: Path) -> dict[str, bytes]:
    """Every file under root except the ledger, by relative path."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not p.relative_to(root).as_posix().startswith(LEDGER + "/")}


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def deploy(cases: dict, prune: bool):
    """Plant, plan and apply in a fresh pair of workspaces; yield what each law needs."""
    root = tempfile.TemporaryDirectory()
    source, target = Path(root.name, "Dev"), Path(root.name, "Prod")
    source.mkdir()
    target.mkdir()
    plant(source, target, cases)
    pipeline = PipelineModel(target="Prod", prune=prune, pairs=[PairRule(source="scripts/**", target="scripts/")])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        plan = build_plan(source, target, pipeline, "p", DeployIgnore.load(source), resolve_pairs(source, pipeline))
    before = files(target)
    ledger = Ledger(target)
    entry = apply_plan(source, target, plan, ledger)
    return root, source, target, plan, before, ledger, entry


TWO_DELETES_THEN_ADD = {"scripts/a.py": ("target_only", b"a"), "scripts/b.py": ("target_only", b"b"),
                        "scripts/c.py": ("source_only", b"c")}
# Prune is refused when the source has no file at all, so it never empties the target (df-gl6).
ONLY_TARGET = {"scripts/a.py": ("target_only", b"a")}


@LAW
@given(CASES, st.booleans())
@example(TWO_DELETES_THEN_ADD, True)
@example(ONLY_TARGET, True)
def test_apply_makes_the_target_match_the_source(cases, prune):
    root, source, target, plan, before, ledger, entry = deploy(cases, prune)
    with root:
        after = files(target)
        pruning = prune and any(state != "target_only" for state, _ in cases.values())
        for rel, (state, blob) in cases.items():
            if state == "target_only":
                assert (rel in after) is not pruning    # pruned only when asked, never from an empty source
            else:
                assert after[rel] == (source / rel).read_bytes()


@LAW
@given(CASES, st.booleans())
@example(TWO_DELETES_THEN_ADD, True)
@example(ONLY_TARGET, True)
def test_rollback_restores_the_target_exactly(cases, prune):
    root, source, target, plan, before, ledger, entry = deploy(cases, prune)
    with root:
        rollback_last(target, ledger)
        assert files(target) == before


@LAW
@given(CASES, st.booleans())
@example(TWO_DELETES_THEN_ADD, True)
@example(ONLY_TARGET, True)
def test_ledger_records_every_write_with_its_snapshots(cases, prune):
    root, source, target, plan, before, ledger, entry = deploy(cases, prune)
    with root:
        recorded = ledger.last()
        assert (recorded is not None) == bool(plan.writable())   # an entry exactly when something was written
        if recorded is None:
            return
        assert recorded == entry
        key = ("source_path", "target_path", "kind", "action", "reason")
        assert [i.model_dump(include=set(key)) for i in recorded.items] == \
               [i.model_dump(include=set(key)) for i in plan.writable()]
        for item in recorded.items:
            prior = before.get(item.target_path)
            assert item.before_sha == (sha(prior) if prior is not None else None)
            if prior is not None:
                assert ledger.read_blob(item.before_sha) == prior
            if item.action == Action.delete:
                assert item.after_sha is None
            else:
                now = (target / item.target_path).read_bytes()
                assert item.after_sha == sha(now) and ledger.read_blob(item.after_sha) == now
