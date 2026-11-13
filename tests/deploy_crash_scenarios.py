"""Scenarios the deploy crash sweep runs in a child process (tests/test_crash_sweep_deploy.py).
Kept out of the test module so the child does not import pytest."""
from pathlib import Path
from dataforge.deploy.apply import apply_plan
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.rollback import rollback_last


def deploy(workdir: Path) -> None:
    """Plan and apply pipeline `p` from <workdir>/Dev to <workdir>/Prod."""
    src, tgt = workdir / "Dev", workdir / "Prod"
    pipeline = load_pipeline(src, "p")
    plan = build_plan(src, tgt, pipeline, "p", DeployIgnore.load(src), resolve_pairs(src, pipeline))
    apply_plan(src, tgt, plan, Ledger(tgt))


def rollback(workdir: Path) -> None:
    """Undo the last deploy of <workdir>/Prod."""
    rollback_last(workdir / "Prod", Ledger(workdir / "Prod"))
