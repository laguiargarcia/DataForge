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
# The measured mutation costs (scripts/testloop/costs.py, df-n8ac.39).
COSTS = ".mutation_costs.json"


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
    """A need may go unchecked only when a failure of it still blocks the merge: it is required
    itself, or another required job reads its result (sonar needs coverage, which tests gates)."""
    jobs = _jobs()
    gated = {n for r in REQUIRED for n in _needs(jobs[r]) if f"needs.{n}.result" in str(jobs[r]["steps"])}
    for name in REQUIRED:
        job = jobs[name]
        if "always()" not in str(job.get("if", "")):
            continue
        steps = str(job["steps"])
        unchecked = [n for n in _needs(job) if n not in REQUIRED | gated and f"needs.{n}.result" not in steps]
        assert unchecked == [], f"{name} passes even when {unchecked} failed"


def _uses(steps: list[dict], action: str) -> list[dict]:
    return [s for s in steps if str(s.get("uses", "")).startswith(action)]


def test_a_pr_mutates_from_mains_cache_and_only_push_or_dispatch_saves_it():
    """df-n8ac.38: a PR restores main's verdicts and never saves its own; the full run of main starts
    empty (its audit of what PRs reused) and saves under the key the PRs restore from. df-n8ac.39: a
    job's items change from run to run, so the cache is one for every item: each PR job restores it
    whole, and mutation-ok saves what the full run's jobs uploaded, merged into the same directory."""
    jobs = _jobs()
    mutation, ok = jobs["mutation"]["steps"], jobs["mutation-ok"]["steps"]
    restore, = _uses(mutation, "actions/cache/restore")
    assert _uses(mutation, "actions/cache/save") == []
    mutate = next(s for s in mutation if s.get("name") == "Mutate")
    upload = next(s for s in _uses(mutation, "actions/upload-artifact") if "mutmut-cache" in s["with"]["name"])
    download = next(s for s in _uses(ok, "actions/download-artifact") if "mutmut-cache" in s["with"]["pattern"])
    save = next(s for s in _uses(ok, "actions/cache/save") if s["with"]["path"] != COSTS)
    cache = "${{ runner.temp }}/mutmut-cache"
    assert restore["if"] == "github.event_name == 'pull_request'"
    for step in (upload, download, save):
        assert "github.event_name != 'pull_request'" in step["if"]
    assert restore["with"]["key"] == save["with"]["key"]
    assert restore["with"]["key"].startswith(restore["with"]["restore-keys"])
    assert restore["with"]["path"] == mutate["env"]["CACHE"] == upload["with"]["path"] == cache
    assert download["with"]["path"] == save["with"]["path"] == cache
    assert download["with"]["merge-multiple"] is True
    assert upload["with"]["name"].startswith(download["with"]["pattern"].removesuffix("*"))
    assert '--cache-dir "$CACHE/$slug"' in mutate["run"]
    assert mutation.index(restore) < mutation.index(mutate) < mutation.index(upload)
    assert ok.index(download) < ok.index(save)


def test_the_matrix_and_mutate_read_the_same_costs_that_only_a_full_run_saves():
    """df-n8ac.39: mutation-targets restores the last full run's costs, packs the jobs from them and
    hands the same file to the mutation jobs, whose shards split by it; mutation-ok saves new ones."""
    jobs = _jobs()
    targets, mutation, ok = (jobs[name]["steps"] for name in ("mutation-targets", "mutation", "mutation-ok"))
    restore, = _uses(targets, "actions/cache/restore")
    save = next(s for s in _uses(ok, "actions/cache/save") if s["with"]["path"] == COSTS)
    assert save["with"]["key"].startswith(restore["with"]["restore-keys"])
    assert restore["with"]["path"] == save["with"]["path"] == COSTS
    assert "github.event_name != 'pull_request'" in save["if"]
    pick = next(s for s in targets if s.get("id") == "pick")
    assert f"--matrix --costs {COSTS}" in pick["run"]
    upload, = (s for s in targets if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    download, = (s for s in mutation if str(s.get("uses", "")).startswith("actions/download-artifact"))
    assert upload["with"]["name"] == download["with"]["name"]
    assert upload["with"]["path"] == COSTS
    assert f"--costs {COSTS}" in next(s for s in mutation if s.get("name") == "Mutate")["run"]
    build = next(s for s in ok if "costs.py" in str(s.get("run", "")))
    assert f"--out {COSTS}" in build["run"]
    assert ok.index(build) < ok.index(save)
