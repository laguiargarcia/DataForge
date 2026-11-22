"""Every CI job blocks the merge (df-n8ac.29).

The repo ruleset requires a few fixed check names; any other job only blocks a merge through one of
them. A job outside that reach goes red while auto-merge carries the PR in anyway, which is how
orders and windows-smoke failed unnoticed. A required job that runs `if: always()` passes even when
a job it needs failed, unless its steps read that need's result.
"""
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dataforge import yamlio


CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

# The ruleset "Main: checks do CI obrigatórios" on GitHub; keep the two in step.
REQUIRED = {"leak-scan", "quality", "tests", "tests-windows", "testloop-tools", "sonar", "mutation-ok"}
# Comments on the PR; it reports the other jobs and gates nothing.
NOT_GATING = {"report"}
# The measured mutation costs (scripts/testloop/costs.py, df-n8ac.39).
COSTS = ".mutation_costs.json"
# The costs a full run split its shards by, saved inside its mutmut cache (df-n8ac.39).
SPLIT = "split-costs.json"


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
    restore = next(s for s in _uses(targets, "actions/cache/restore") if s["with"]["path"] == COSTS)
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
    assert f"costs.py > {COSTS}" in build["run"]
    assert ok.index(build) < ok.index(save)


def test_a_pr_splits_by_the_costs_that_built_the_cache_it_reuses():
    """df-n8ac.39: a push saves, inside the mutmut cache, the costs file it split the shards by; a PR
    restores that cache in mutation-targets and packs, splits and hands the jobs that same file. Split by
    newer costs, a function would change shard and lose its cached verdicts (df-n8ac.38)."""
    jobs = _jobs()
    targets, mutation, ok = (jobs[name]["steps"] for name in ("mutation-targets", "mutation", "mutation-ok"))
    cache = "${{ runner.temp }}/mutmut-cache"
    verdicts = next(s for s in _uses(targets, "actions/cache/restore") if s["with"]["path"] == cache)
    job_restore, = _uses(mutation, "actions/cache/restore")
    save = next(s for s in _uses(ok, "actions/cache/save") if s["with"]["path"] == cache)
    assert verdicts["if"] == "github.event_name == 'pull_request'"
    assert verdicts["with"] == job_restore["with"]
    assert verdicts["with"]["key"] == save["with"]["key"]
    pick = next(s for s in targets if s.get("id") == "pick")
    assert targets.index(verdicts) < targets.index(pick)
    assert f'split="$RUNNER_TEMP/mutmut-cache/{SPLIT}"' in pick["run"]
    assert f'cp -f "$split" {COSTS}' in pick["run"]
    assert pick["run"].index("cp -f") < pick["run"].index("--matrix")
    split = next(s for s in _uses(ok, "actions/download-artifact") if s["with"].get("name") == "mutation-costs")
    keep = next(s for s in ok if f"$RUNNER_TEMP/mutmut-cache/{SPLIT}" in str(s.get("run", "")))
    assert f'"$RUNNER_TEMP/split/{COSTS}"' in keep["run"]
    assert split["with"]["path"] == "${{ runner.temp }}/split"
    assert ok.index(split) < ok.index(keep) < ok.index(save)
    assert keep["if"] == split["if"] == save["if"]


# --- The steps that gate are run, not read (df-g2ot.1) ---------------------------------------------
# A text search finds a line that was deleted, not a `|| exit 1` turned into `|| echo`, or a pipe to
# tee that no longer fails because the shell lost pipefail. So each gating step's script runs here, in
# bash, the way GitHub runs it, and must exit non-zero when what it gates failed.

pytestmark_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="runs the steps' scripts with bash"
)
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
NEED_RESULT = re.compile(r"needs\.([\w-]+)\.result")
# The tools a step pipes into tee; each is replaced by a stub that exits with $STUB_RC (jq lists a target).
STUBBED = ("uv", "npm", "pytest", "python", "jq")
STUB = '#!/bin/bash\n[ "${0##*/}" = jq ] && echo target\nexit "${STUB_RC:-0}"\n'
# defaults.run.shell of the GitHub Actions docs, with the script path in place of {0}.
SHELLS = {
    "default": ["bash", "-e"],
    "bash": ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
}


def _all_jobs(jobs: dict) -> set[str]:
    reached, todo = set(), list(REQUIRED)
    while todo:
        name = todo.pop()
        if name not in reached:
            reached.add(name)
            todo.extend(_needs(jobs[name]))
    return reached


def _shell(job: dict, step: dict) -> list[str] | None:
    shell = step.get("shell") or job.get("defaults", {}).get("run", {}).get("shell")
    if shell is None:
        return None if "windows" in str(job.get("runs-on")) else SHELLS["default"]
    return SHELLS.get(shell)


def _run_step(job: dict, step: dict, workdir: Path, needs: dict[str, str], stub_rc: int) -> int:
    """Run the step's script with each ${{ needs.X.result }} replaced from `needs` and any other
    expression by "x"; the commands it pipes into tee are the stubs, exiting `stub_rc`."""
    def fill(text):
        return EXPRESSION.sub(lambda m: needs.get(m[1].strip().removeprefix("needs.").removesuffix(".result"), "x"), text)
    stubs = workdir / "stubs"
    stubs.mkdir(exist_ok=True)
    for tool in STUBBED:
        (stubs / tool).write_text(STUB)
        (stubs / tool).chmod(stat.S_IRWXU)
    script = workdir / "step.sh"
    script.write_text(fill(step["run"]))
    env = {k: fill(str(v)) for k, v in step.get("env", {}).items()}
    env |= {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(workdir), "STUB_RC": str(stub_rc),
            "GITHUB_STEP_SUMMARY": str(workdir / "summary"), "RUNNER_TEMP": str(workdir),
            "GITHUB_WORKSPACE": str(workdir)}
    shell = _shell(job, step)
    assert shell is not None, f"{step.get('name')}: no bash shell"
    return subprocess.run([*shell, str(script)], cwd=workdir, env=env, capture_output=True, check=False).returncode


def _needs_checks(jobs: dict, tmp: Path) -> list[str]:
    """Problems of the steps reading `needs.<n>.result`: all success must pass, and each need failed
    or cancelled, one at a time, must fail."""
    problems = []
    for name in sorted(REQUIRED):
        job = jobs[name]
        for step in job.get("steps", []):
            used = sorted(set(NEED_RESULT.findall(str(step.get("env", "")) + str(step.get("run", "")))))
            if not used:
                continue
            label = f"{name} / {step.get('name')}"
            ok = dict.fromkeys(used, "success")
            if _run_step(job, step, tmp, ok, 0) != 0:
                problems.append(f"{label} fails when every need succeeded")
            for need in used:
                for result in ("failure", "cancelled"):
                    if _run_step(job, step, tmp, ok | {need: result}, 0) == 0:
                        problems.append(f"{label} passes when {need} is {result}")
    return problems


def _tee_checks(jobs: dict, tmp: Path) -> list[str]:
    """Problems of the steps that pipe a command into tee: a failing command must fail the step."""
    problems = []
    for name in sorted(_all_jobs(jobs)):
        job = jobs[name]
        for step in job.get("steps", []):
            if "| tee" not in str(step.get("run", "")):
                continue
            label = f"{name} / {step.get('name')}"
            if _shell(job, step) is None:
                problems.append(f"{label} pipes into tee without bash")
            elif _run_step(job, step, tmp, {}, 0) != 0:
                problems.append(f"{label} fails when its commands succeed")
            elif _run_step(job, step, tmp, {}, 1) == 0:
                problems.append(f"{label} passes when its commands fail")
    return problems


def _broken(*swaps: tuple[str, str]) -> dict:
    text = CI.read_text(encoding="utf-8")
    for old, new in swaps:
        assert old in text, old
        text = text.replace(old, new)
    return yamlio.load(text)["jobs"]


@pytestmark_bash
def test_a_step_reading_a_need_fails_when_the_need_did_not_succeed(tmp_path):
    assert _needs_checks(_jobs(), tmp_path) == []


@pytestmark_bash
def test_a_step_piping_into_tee_fails_when_the_command_fails(tmp_path):
    assert _tee_checks(_jobs(), tmp_path) == []


@pytestmark_bash
def test_the_checks_cover_the_steps_that_matter(tmp_path):
    jobs = _jobs()
    read = {n for r in REQUIRED for s in jobs[r].get("steps", []) for n in NEED_RESULT.findall(str(s.get("env", "")))}
    assert {"mutation-targets", "mutation", "tests-shard", "windows-smoke"} <= read
    piped = [s["name"] for n in _all_jobs(jobs) for s in jobs[n].get("steps", []) if "| tee" in str(s.get("run", ""))]
    assert "Gate each target on its shards" in piped


@pytestmark_bash
@pytest.mark.parametrize(
    ("check", "swaps"),
    [
        (_needs_checks, [('[ "$PICK" = "success" ] || exit 1', '[ "$PICK" = "success" ] || echo')]),
        (_needs_checks, [('[ "$MUTATION" = "skipped" ] || exit 1', '[ "$MUTATION" = "skipped" ] || echo')]),
        (_tee_checks, [("        shell: bash\n        env: { TARGETS", "        env: { TARGETS")]),
    ],
    ids=["pick-not-gated", "mutation-not-gated", "tee-without-pipefail"],
)
def test_the_checks_catch_the_bugs_df_g2ot_injected(check, swaps, tmp_path):
    assert check(_broken(*swaps), tmp_path) != []
