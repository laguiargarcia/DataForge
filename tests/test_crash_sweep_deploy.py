"""Crash sweep of deploy apply and rollback (df-ahua.5): the process is killed at every write and
the state it leaves is checked against what the deploy code and its decisions promise. The
decisions (D16, D18) are in docs/superpowers/plans/deployment-pipeline/DECISIONS.md."""
import hashlib
import shutil
from pathlib import Path
import pytest
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.models import LEDGER_REL
from tests.crash_sweep import Step, crash_sweep
from tests.deploy_crash_scenarios import deploy, rollback

_PIPELINE = b"target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"
_KEEP = {"data/keep.txt": b"not a deploy target\n"}
_OLD = {"scripts/mod.py": b"old mod\n" * 4, "scripts/gone.py": b"old gone\n" * 4, **_KEEP}
_NEW = {"scripts/mod.py": b"new mod\n" * 4, "scripts/new/add.py": b"new add\n" * 4, **_KEEP}
_SOURCE = {"scripts/mod.py": _NEW["scripts/mod.py"], "scripts/new/add.py": _NEW["scripts/new/add.py"],
           "deploy/p.yaml": _PIPELINE}
_OUTSIDE = b"outside both workspaces\n"
_TARGETS = ("scripts/mod.py", "scripts/new/add.py", "scripts/gone.py")

# What makes deploy state durable: the temp file and the ledger blobs (write_bytes), the swap
# into place (os.replace), deletes (unlink, rmdir) and the two rewrites of the deploy log.
_WRITE_POINTS = ["pathlib.Path.write_bytes", "os.replace", "pathlib.Path.unlink",
                 "pathlib.Path.rmdir", "dataforge.deploy.ledger.Ledger.append",
                 "dataforge.deploy.ledger.Ledger.drop_last"]
_TORN = ("pathlib.Path.write_bytes",)


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under root except the ledger, as {relative path: content}."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not p.relative_to(root).as_posix().startswith(LEDGER_REL)}


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(content)


def _before_apply(root: Path, name: str) -> Path:
    """A workdir where one deploy will ADD new/add.py, MODIFY mod.py and DELETE gone.py."""
    workdir = root / name
    _write_tree(workdir / "Dev", _SOURCE)
    _write_tree(workdir / "Prod", _OLD)
    _write_tree(workdir, {"outside.txt": _OUTSIDE})
    return workdir


def _after_apply(root: Path, name: str) -> Path:
    workdir = _before_apply(root, name)
    deploy(workdir)
    return workdir


class _Findings:
    """What each invariant found at each crashed step."""

    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.broken: dict[str, list[str]] = {}

    def check(self, invariant: str, step: Step, verdict) -> None:
        """`verdict()` returns a description of the problem, or "" when the invariant holds."""
        try:
            problem = verdict()
        except Exception as exc:   # noqa: BLE001 -- recovery that raises is a finding, not a test error
            problem = repr(exc)
        where = f"k={step.k} {step.point}{' (torn)' if step.torn else ''}"
        self.broken.setdefault(invariant, []).extend([f"{where}: {problem}"] if problem else [])

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
    bad = [p.name[:8] for p in Ledger(tgt).objects.rglob("*")
           if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() != p.name]
    return f"blobs whose content is not their name: {bad}" if bad else ""


def _rollback_restores(workdir: Path) -> str:
    """On a copy: if the crash changed a target file, rollback brings _OLD back. A leftover temp
    file is not counted here; it belongs to the convergence check."""
    copy = workdir.parent / (workdir.name + "-rollback")
    shutil.copytree(workdir / "Prod", copy / "Prod")

    def files() -> dict[str, bytes]:
        return {rel: data for rel, data in _tree(copy / "Prod").items()
                if not rel.endswith(".deploytmp")}

    if files() == _OLD:
        return ""
    rollback(copy)
    return _differs(files(), _OLD)


def _redeploy_converges(workdir: Path) -> str:
    deploy(workdir)
    return _differs(_tree(workdir / "Prod"), _NEW)


@pytest.fixture(scope="module")
def apply_sweep(tmp_path_factory) -> _Findings:
    root, found = tmp_path_factory.mktemp("apply"), _Findings()

    def check_recovered(workdir: Path, step: Step) -> None:
        found.check("atomic", step, lambda: _mixed_targets(workdir / "Prod"))
        found.check("outside", step, lambda: _outside(workdir))
        found.check("blobs", step, lambda: _bad_blobs(workdir / "Prod"))
        found.check("rollback", step, lambda: _rollback_restores(workdir))
        found.check("converges", step, lambda: _redeploy_converges(workdir))   # last: it writes

    found.steps = crash_sweep("tests.deploy_crash_scenarios:deploy",
                              lambda name: _before_apply(root, name), _WRITE_POINTS,
                              check_recovered, torn=_TORN)
    return found


def test_apply_sweep_reaches_every_kind_of_write(apply_sweep):
    assert {"pathlib.Path.write_bytes", "os.replace", "pathlib.Path.unlink",
            "dataforge.deploy.ledger.Ledger.append"} <= {s.point for s in apply_sweep.steps}


def test_apply_crash_leaves_each_target_old_or_new(apply_sweep):
    # Promised by apply._atomic_write ("never leaves a half-written target") and D16.
    apply_sweep.assert_holds("atomic")


def test_apply_crash_touches_nothing_outside_the_paired_paths(apply_sweep):
    # Promised by apply_plan: only plan.writable() items are written (df-m1dk path guard).
    apply_sweep.assert_holds("outside")


@pytest.mark.xfail(strict=True, reason="WIP df-ahua.5: bead not filed yet -- a torn blob write "
                   "stays under its hash name and Ledger.write_blob never rewrites it")
def test_apply_crash_keeps_every_blob_true_to_its_hash(apply_sweep):
    # Promised by Ledger ("content-addressed blob store") and D18 (never a partial restore).
    apply_sweep.assert_holds("blobs")


@pytest.mark.xfail(strict=True, reason="WIP df-ahua.5: bead not filed yet -- the ledger entry is "
                   "appended only after the last write, so a killed apply has none")
def test_apply_crash_stays_rollback_recoverable(apply_sweep):
    # Promised by apply_plan's docstring ("a partial apply stays rollback-recoverable", law #4)
    # and D16 ("Crash-safe apply").
    apply_sweep.assert_holds("rollback")


@pytest.mark.xfail(strict=True, reason="WIP df-ahua.5: bead not filed yet -- a leftover "
                   ".deploytmp is planned as a DELETE and the next apply raises FileNotFoundError")
def test_redeploy_after_apply_crash_converges(apply_sweep):
    # Promised by build_plan + apply being a sync to the source state (a second deploy is a no-op).
    apply_sweep.assert_holds("converges")
