"""Crash sweep of deploy apply and rollback (df-ahua.5): the process is killed at every write and
the state it leaves is checked against what the deploy code and its decisions promise. D16/D18
are in docs/superpowers/plans/deployment-pipeline/DECISIONS.md, the four laws in the deploy spec
(docs/superpowers/specs/2026-06-02-workspace-deployment-pipeline-design.md, section 3)."""
import hashlib
import shutil
from pathlib import Path
import pytest
from dataforge.deploy.ledger import Ledger, is_temp_name
from dataforge.deploy.models import LEDGER_REL
from tests.crash_sweep import Step, WritePoints, crash_sweep
from tests.deploy_crash_scenarios import deploy, rollback

_PIPELINE = b"target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"
_KEEP = {"data/keep.txt": b"not a deploy target\n"}
_OLD = {"scripts/mod.py": b"old mod\n" * 4, "scripts/gone.py": b"old gone\n" * 4, **_KEEP}
_NEW = {"scripts/mod.py": b"new mod\n" * 4, "scripts/new/add.py": b"new add\n" * 4, **_KEEP}
_SOURCE = {"scripts/mod.py": _NEW["scripts/mod.py"], "scripts/new/add.py": _NEW["scripts/new/add.py"],
           "deploy/p.yaml": _PIPELINE}
_OUTSIDE = b"outside both workspaces\n"
_TARGETS = ("scripts/mod.py", "scripts/new/add.py", "scripts/gone.py")

# What makes deploy state durable: the temp files of targets, blobs and the rewritten deploy log
# (write_bytes), the swap into place (os.replace), deletes (unlink, rmdir) and the log append,
# which the ledger writes through open(..., "ab"). Every other log write (completing the entry,
# dropping the last one) rewrites the whole log through a temp file + os.replace.
_LOG_OPEN = "dataforge.deploy.ledger.open"
_POINTS = WritePoints(["pathlib.Path.write_bytes", "os.replace", "pathlib.Path.unlink",
                       "pathlib.Path.rmdir", _LOG_OPEN],
                      torn=("pathlib.Path.write_bytes",), after=(_LOG_OPEN,))


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under root except the ledger, as {relative path: content}."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not p.relative_to(root).as_posix().startswith(LEDGER_REL)}


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(content)


def _before_apply(root: Path, name: str) -> Path:
    """A Prod at _OLD with one earlier deploy in its log (it added gone.py; mod.py and keep.txt
    were put there by hand). The deploy under test will ADD new/add.py, MODIFY mod.py (a new
    'before' blob) and DELETE gone.py (its 'before' blob is already in the ledger)."""
    workdir = root / name
    _write_tree(workdir / "Dev", {"scripts/gone.py": _OLD["scripts/gone.py"], "deploy/p.yaml": _PIPELINE})
    deploy(workdir)
    (workdir / "Dev/scripts/gone.py").unlink()
    _write_tree(workdir / "Dev", _SOURCE)
    _write_tree(workdir / "Prod", _OLD)
    _write_tree(workdir, {"outside.txt": _OUTSIDE})
    return workdir


def _after_apply(root: Path, name: str) -> Path:
    workdir = _before_apply(root, name)
    deploy(workdir)
    return workdir


def _log_ids(tgt: Path) -> list[str]:
    return [entry.id for entry in Ledger(tgt).entries()]


class _Findings:
    """What each invariant found at each crashed step."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.steps: list[Step] = []
        self.broken: dict[str, list[str]] = {}

    def check(self, invariant: str, step: Step, verdict) -> None:
        """`verdict()` returns a description of the problem, or "" when the invariant holds."""
        try:
            problem = verdict()
        except Exception as exc:   # noqa: BLE001 -- recovery that raises is a finding, not a test error
            problem = repr(exc)
        self.broken.setdefault(invariant, []).extend([f"{step}: {problem}"] if problem else [])

    def assert_holds(self, invariant: str) -> None:
        assert self.broken[invariant] == [], "\n".join(self.broken[invariant])


def _differs(found: dict[str, bytes], expected: dict[str, bytes]) -> str:
    return "" if found == expected else f"left {found}"


def _mixed_targets(tgt: Path) -> str:
    found = _tree(tgt)
    bad = [rel for rel in _TARGETS if found.get(rel) not in (_OLD.get(rel), _NEW.get(rel))]
    return f"neither old nor new: {bad}" if bad else ""


def _outside(workdir: Path) -> str:
    same = (_tree(workdir / "Dev") == _SOURCE and (workdir / "outside.txt").read_bytes() == _OUTSIDE
            and (workdir / "Prod/data/keep.txt").read_bytes() == _KEEP["data/keep.txt"])
    return "" if same else "a file outside the paired target paths changed"


def _bad_blobs(tgt: Path) -> str:
    """Blobs (files named by a hash) whose content is not that hash. A temp file a killed blob
    write left behind is not a blob: nothing addresses it, and the next write of that content
    replaces it."""
    bad = [p.name[:8] for p in Ledger(tgt).objects.rglob("*")
           if p.is_file() and not is_temp_name(p.name)
           and hashlib.sha256(p.read_bytes()).hexdigest() != p.name]
    return f"blobs whose content is not their name: {bad}" if bad else ""


def _files(tgt: Path) -> dict[str, bytes]:
    """The target's files without a leftover temp file (that is the convergence check's job)."""
    return {rel: data for rel, data in _tree(tgt).items() if not rel.endswith(".deploytmp")}


def _rollback_restores(workdir: Path) -> str:
    """On a copy: if the crash changed a target file, one rollback brings _OLD back."""
    copy = workdir.parent / (workdir.name + "-rollback")
    shutil.copytree(workdir / "Prod", copy / "Prod")
    if _files(copy / "Prod") == _OLD:
        return ""
    rollback(copy)
    return _differs(_files(copy / "Prod"), _OLD)


def _redeploy_converges(workdir: Path) -> str:
    """Deploying again reaches _NEW. Only when the crash left a .deploytmp is one failed deploy
    tolerated: the next prune deploy then raises once (open question, df-ahua.5.5)."""
    if _files(workdir / "Prod") == _tree(workdir / "Prod"):
        deploy(workdir)
    else:
        try:
            deploy(workdir)
        except FileNotFoundError:
            deploy(workdir)
    return _differs(_tree(workdir / "Prod"), _NEW)


@pytest.fixture(scope="module")
def apply_sweep(tmp_path_factory) -> _Findings:
    found = _Findings(tmp_path_factory.mktemp("apply"))

    def check_recovered(workdir: Path, step: Step) -> None:
        found.check("atomic", step, lambda: _mixed_targets(workdir / "Prod"))
        found.check("outside", step, lambda: _outside(workdir))
        found.check("blobs", step, lambda: _bad_blobs(workdir / "Prod"))
        found.check("rollback", step, lambda: _rollback_restores(workdir))
        found.check("converges", step, lambda: _redeploy_converges(workdir))   # last: it writes

    found.steps = crash_sweep("tests.deploy_crash_scenarios:deploy",
                              lambda name: _before_apply(found.root, name), _POINTS,
                              check_recovered)
    return found


def test_apply_sweep_reaches_every_kind_of_write(apply_sweep):
    assert {"pathlib.Path.write_bytes", "os.replace", "pathlib.Path.unlink", _LOG_OPEN} <= {
        s.point for s in apply_sweep.steps}
    dry = apply_sweep.root / "dry/Prod"   # the uncrashed run did the whole deploy
    assert (_tree(dry), len(_log_ids(dry))) == (_NEW, 2)


def test_apply_crash_leaves_each_target_old_or_new(apply_sweep):
    # Promised by ledger.atomic_write ("a failed write never leaves a half-written file") and
    # D16 ("atomic per-file writes").
    apply_sweep.assert_holds("atomic")


def test_apply_crash_touches_nothing_outside_the_paired_paths(apply_sweep):
    # Promised by apply_plan: only plan.writable() items are written (df-m1dk path guard).
    apply_sweep.assert_holds("outside")


def test_redeploy_after_apply_crash_reaches_the_source_state(apply_sweep):
    # Apply makes the target match the source (spec section 5, and
    # test_deploy_apply_properties::test_apply_makes_the_target_match_the_source); D16 says the
    # laws hold under failure, so a crash must not wedge the next deploys.
    apply_sweep.assert_holds("converges")


def test_apply_crash_keeps_every_blob_true_to_its_hash(apply_sweep):
    # df-ahua.5.2. Promised by Ledger ("content-addressed blob store"), law #4 (every overwrite/delete is
    # recoverable via the ledger) and D18 (rollback must not partially restore).
    apply_sweep.assert_holds("blobs")


def test_apply_crash_stays_rollback_recoverable(apply_sweep):
    # df-ahua.5.1. Promised by apply_plan's docstring ("a partial apply stays rollback-recoverable", law #4)
    # and D16 ("Crash-safe apply").
    apply_sweep.assert_holds("rollback")


@pytest.fixture(scope="module")
def rollback_sweep(tmp_path_factory) -> _Findings:
    found, first = _Findings(tmp_path_factory.mktemp("rollback")), {}

    def make_workdir(name: str) -> Path:
        workdir = _after_apply(found.root, name)
        first[workdir] = _log_ids(workdir / "Prod")[:1]   # the earlier deploy, never to be lost
        return workdir

    def check_recovered(workdir: Path, step: Step) -> None:
        tgt = workdir / "Prod"
        found.check("atomic", step, lambda: _mixed_targets(tgt))
        found.check("outside", step, lambda: _outside(workdir))
        found.check("rerun", step, lambda: rollback(workdir) or _differs(_tree(tgt), _OLD))
        found.check("history", step, lambda: "" if _log_ids(tgt) == first[workdir]
                    else f"log after the rerun: {_log_ids(tgt)}, expected {first[workdir]}")

    found.steps = crash_sweep("tests.deploy_crash_scenarios:rollback", make_workdir, _POINTS,
                              check_recovered)
    return found


def test_rollback_sweep_reaches_every_kind_of_write(rollback_sweep):
    # Rollback never appends to the log: it drops the entry with a whole-log rewrite, so it
    # reaches no _LOG_OPEN.
    assert {"pathlib.Path.write_bytes", "os.replace", "pathlib.Path.unlink",
            "pathlib.Path.rmdir"} <= {s.point for s in rollback_sweep.steps}
    dry = rollback_sweep.root / "dry/Prod"   # the uncrashed run did the whole rollback
    assert (_tree(dry), len(_log_ids(dry))) == (_OLD, 1)


def test_rollback_crash_leaves_each_target_old_or_new(rollback_sweep):
    # Promised by ledger.atomic_write, which rollback restores through, and D18 ("must not ...
    # partially restore").
    rollback_sweep.assert_holds("atomic")


def test_rollback_crash_touches_nothing_outside_the_deployed_paths(rollback_sweep):
    # rollback_last only touches the paths of the entry it undoes (df-m1dk path guard).
    rollback_sweep.assert_holds("outside")


def test_rerunning_rollback_after_a_crash_restores_the_pre_deploy_state(rollback_sweep):
    # D18: rollback "must not wedge or partially restore". rollback_last drops the entry last,
    # so after a crash the entry is still there and running rollback again finishes the job.
    rollback_sweep.assert_holds("rerun")


def test_rollback_crash_keeps_the_earlier_deploys_in_the_log(rollback_sweep):
    # df-ahua.5.3. Ledger: "append-only deploy log"; rollback_last drops only the entry it undid (LIFO), and
    # law #4 needs the earlier entries to undo the earlier deploys.
    rollback_sweep.assert_holds("history")


def test_a_torn_last_log_line_does_not_wedge_rollback_or_deploy(tmp_path):
    # df-ahua.5.4. A crash or a full disk while Ledger.append writes can leave half a JSON line. D18: rollback
    # "must not wedge"; D16: the laws hold under failure. The deploy after must still work. Not swept: the line is written by a
    # file object, which the sweep cannot cut, so the torn state is written here directly.
    workdir = _after_apply(tmp_path, "w")
    log = Ledger(workdir / "Prod").log
    first, last = log.read_text(encoding="utf-8").splitlines(keepends=True)
    log.write_text(first + last[:len(last) // 2], encoding="utf-8")
    assert _log_ids(workdir / "Prod")   # read first by every apply, rollback and status
    deploy(workdir)
