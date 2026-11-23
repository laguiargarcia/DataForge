"""No release reaches uv.lock in its first days, and the lock a PR brings is its pyproject's (df-is14).

PR #170 (Renovate's lock file maintenance) merged by itself with releases a few hours old: Renovate
cannot date what uv picks, its own status for the age is not a required check, and no CI step
compared the lock with pyproject.toml. The age is now uv's own setting, and a required job checks
the lock.
"""
import tomllib
from pathlib import Path

from dataforge import yamlio

ROOT = Path(__file__).resolve().parents[1]
# A job the repo ruleset requires (tests/test_ci_workflow.py, REQUIRED).
REQUIRED_JOB = "quality"


def _toml(name: str) -> dict:
    return tomllib.loads((ROOT / name).read_text(encoding="utf-8"))


def test_uv_refuses_a_release_younger_than_three_days():
    assert _toml("pyproject.toml")["tool"]["uv"]["exclude-newer"] == "3 days"


def test_the_lock_was_resolved_under_that_age():
    assert _toml("uv.lock")["options"]["exclude-newer-span"] == "P3D"


def test_a_required_job_checks_the_lock_against_pyproject_unconditionally():
    workflow = yamlio.load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"][REQUIRED_JOB]
    checks = [step for step in job["steps"] if step.get("run", "").strip() == "uv lock --check"]
    assert len(checks) == 1
    assert "if" not in job
    assert {"if", "continue-on-error"} & set(checks[0]) == set()
