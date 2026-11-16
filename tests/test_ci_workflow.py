"""Every CI job blocks the merge (df-n8ac.29).

The repo ruleset requires a few fixed check names; any other job only blocks a merge through one of
them. A job outside that reach goes red while auto-merge carries the PR in anyway, which is how
orders and windows-smoke failed unnoticed. A required job that runs `if: always()` passes even when
a job it needs failed, unless its steps read that need's result.
"""
from pathlib import Path

from dataforge import yamlio

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

# The ruleset "Main: checks do CI obrigatórios" on GitHub; keep the two in step.
REQUIRED = {"leak-scan", "quality", "tests", "tests-windows", "testloop-tools", "sonar", "mutation-ok"}
# Comments on the PR; it reports the other jobs and gates nothing.
NOT_GATING = {"report"}


def _jobs() -> dict:
    return yamlio.load(CI.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def test_the_required_checks_are_jobs():
    assert set(_jobs()) >= REQUIRED


def test_every_job_is_required_or_needed_by_a_required_job():
    jobs = _jobs()
    reached, todo = set(), list(REQUIRED)
    while todo:
        name = todo.pop()
        if name not in reached:
            reached.add(name)
            todo.extend(_needs(jobs[name]))
    assert set(jobs) - reached - NOT_GATING == set()


def test_a_required_job_that_always_runs_checks_each_need_it_gates():
    for name in REQUIRED:
        job = _jobs()[name]
        if "always()" not in str(job.get("if", "")):
            continue
        steps = str(job["steps"])
        unchecked = [n for n in _needs(job) if n not in REQUIRED and f"needs.{n}.result" not in steps]
        assert unchecked == [], f"{name} passes even when {unchecked} failed"
