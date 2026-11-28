"""Every CI job blocks the merge (df-n8ac.29).

The repo ruleset requires a few fixed check names; any other job only blocks a merge through one of
them. A job outside that reach goes red while auto-merge carries the PR in anyway, which is how
orders and windows-smoke failed unnoticed. A required job that runs `if: always()` passes even when
a job it needs failed, unless its steps read that need's result.
"""
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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
    return _with_workflow_defaults(yamlio.load(CI.read_text(encoding="utf-8")))


def _with_workflow_defaults(workflow: dict) -> dict:
    """The workflow's jobs, each with the workflow's defaults.run.shell where it sets none of its own:
    the checks read a job alone."""
    shell = workflow.get("defaults", {}).get("run", {}).get("shell")
    jobs = workflow["jobs"]
    for job in jobs.values() if shell else ():
        job.setdefault("defaults", {}).setdefault("run", {}).setdefault("shell", shell)
    return jobs


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def _gating_jobs(jobs: dict) -> set[str]:
    """The jobs reached from the required ones through `needs`."""
    reached, todo = set(), list(REQUIRED)
    while todo:
        name = todo.pop()
        if name not in reached:
            reached.add(name)
            todo.extend(_needs(jobs[name]))
    return reached


def test_the_required_checks_are_jobs():
    assert set(_jobs()) >= REQUIRED


def test_every_job_is_required_or_needed_by_a_required_job():
    jobs = _jobs()
    assert set(jobs) - _gating_jobs(jobs) - NOT_GATING == set()


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


# --- The steps that gate are run, not read (df-g2ot.1, df-qn1u) ------------------------------------
# A text search finds a line that was deleted, not a `|| exit 1` turned into `|| echo`, or a pipe to
# tee that no longer fails because the shell lost pipefail. So each gating step's script runs here, in
# bash, the way GitHub runs it, and must exit non-zero when what it gates failed. A step also stops
# gating when it is skipped (its `if:` or its job's) or allowed to fail (continue-on-error): GitHub
# counts both as passed, so the `if:`s are evaluated and continue-on-error is refused.

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="runs the steps' scripts with bash"
)
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
NEED_RESULT = re.compile(r"needs\.([\w-]+)\.result")
EXIT = re.compile(r"\bexit\b")
# The tools a gating step runs; each is replaced by a stub. jq lists a target. Any other counts its
# calls in $STUB_CALLS, and the call number $STUB_FAIL fails alone (df-qn1u.1; failing them all, an
# earlier command stopped the script under -e before the pipe that had lost pipefail): it exits 1,
# except git, which reports a changed file (dist differs).
STUBBED = ("uv", "npm", "npx", "pytest", "python", "python3", "jq", "git")
STUB = """#!/bin/bash
tool="${0##*/}"
if [ "$tool" = jq ]; then echo target; exit 0; fi
echo "$tool" >> "$STUB_CALLS"
[ "$(wc -l < "$STUB_CALLS")" -eq "$STUB_FAIL" ] || exit 0
if [ "$tool" = git ]; then echo " M dist/index.html"; else exit 1; fi
"""
# The actions that are a gate themselves: the step fails the job when the scan finds something.
GATING_ACTIONS = ("gitleaks/gitleaks-action", "SonarSource/sonarqube-scan-action")
# The secrets a gating step's `if:` may read, taken as set, and why a run without them may skip it.
SECRETS_SET = {
    "SONAR_TOKEN": "a fork's PR gets no secrets, so the scan cannot run there; a PR of this repo has it",
}
# defaults.run.shell of the GitHub Actions docs, with the script path in place of {0}.
SHELLS = {
    "default": ["bash", "-e"],
    "bash": ["bash", "--noprofile", "--norc", "-eo", "pipefail"],
}
# The needs whose result may be `skipped`, per job; anywhere else skipped means nothing ran.
# mutation-ok's mutation is skipped when no target was picked. PICK gates a failed pick; a mutation
# job skipped while targets were picked is held only by the gate, which refuses a target with no mutate
# result (scripts/testloop/gate_cmd.py, _load).
SKIPPED_OK = {"mutation-ok": {"mutation"}}
# The only continue-on-error a gating job may have, (job, artifact downloaded): why it is safe.
CONTINUE_OK = {
    ("coverage", "coverage-part-*"): "a part that died early leaves none; its own job fails tests",
    ("coverage", "durations-linux-*"): "durations only order the next split",
    ("tests-windows", "durations-windows-*"): "durations only order the next split",
    ("sonar", "coverage"): "Sonar analyzes without coverage; tests gates the parts that write it",
    ("mutation-ok", "testloop-results-*"): "the gate exits 2 on a missing shard",
    ("mutation-ok", "mutmut-cache-*"): "only the cache for the next PRs",
}
# The `if:` a step reading needs.X.result may have: it must run whatever those needs did.
NEEDS_STEP_IFS = (None, "always()")
# The status functions of an `if:`: a step whose earlier steps passed, and a job whose need failed.
RUNNING = {"always": True, "cancelled": False, "success": True, "failure": False}
# A step after a failed one, and any step of a cancelled run.
STEP_FAILED = {"always": True, "cancelled": False, "success": False, "failure": True}
CANCELLED = {"always": True, "cancelled": True, "success": False, "failure": False}
NEED_FAILED = {"always": True, "cancelled": False, "success": False, "failure": True}
EVENTS = ("pull_request", "push")
TOKEN = re.compile(r"\s*(?:'((?:[^']|'')*)'|(&&|\|\||==|!=|!|\(|\))|([A-Za-z_][\w.-]*))")
STATUS = re.compile(r"\b(?:always|cancelled|success|failure)\(\)")


def _tokens(expr: str) -> list[tuple[str, str]]:
    tokens, at, expr = [], 0, expr.strip()
    while at < len(expr):
        match = TOKEN.match(expr, at)
        if not match:
            raise ValueError(f"cannot read {expr!r} at {at}")
        string, op, name = match.groups()
        tokens.append(("str", string.replace("''", "'")) if string is not None else ("op", op) if op else ("name", name))
        at = match.end()
    return tokens


class _Expression:
    """A GitHub Actions expression, in the subset ci.yml uses: strings, names looked up in `names`
    (a name it does not hold is a ValueError: the test would only guess its value), ==, != (of two
    values of one type: GitHub coerces mixed ones to numbers), !, &&, || (which return an operand, as
    GitHub's do), parentheses and the status functions in `status`."""

    def __init__(self, expr: str, names: dict, status: dict):
        self.expr, self.tokens, self.names, self.status = expr, _tokens(expr), names, status

    def value(self):
        value = self.either()
        if self.tokens:
            raise ValueError(f"{self.expr!r}: left {self.tokens}")
        return value

    def peek(self):
        return self.tokens[0] if self.tokens else None

    def either(self):
        value = self.both()
        while self.peek() == ("op", "||"):
            self.tokens.pop(0)
            right = self.both()
            value = value or right
        return value

    def both(self):
        value = self.compare()
        while self.peek() == ("op", "&&"):
            self.tokens.pop(0)
            right = self.compare()
            value = value and right
        return value

    def compare(self):
        left = self.unary()
        if self.peek() not in (("op", "=="), ("op", "!=")):
            return left
        negate = self.tokens.pop(0)[1] == "!="
        right = self.unary()
        if type(left) is not type(right):
            raise ValueError(f"{self.expr!r} compares {left!r} with {right!r}, which GitHub coerces to numbers")
        if isinstance(left, str):
            left, right = left.lower(), right.lower()
        return (left == right) != negate

    def unary(self):
        if self.peek() == ("op", "!"):
            self.tokens.pop(0)
            return not self.unary()
        return self.atom()

    def atom(self):
        if not self.tokens:
            raise ValueError(f"{self.expr!r} ends early")
        kind, value = self.tokens.pop(0)
        if kind == "str":
            return value
        if value == "(":
            inner = self.either()
            self.expect(")")
            return inner
        if self.peek() == ("op", "("):
            self.expect("(")
            self.expect(")")
            return self.status[value]
        if kind != "name":
            raise ValueError(f"{self.expr!r}: unexpected {value!r}")
        literals = {"true": True, "false": False, "null": None}
        if value not in literals and value not in self.names:
            raise ValueError(f"{self.expr!r}: the test gives no value for {value}")
        return literals[value] if value in literals else self.names[value]

    def expect(self, op: str):
        if (self.tokens or [None])[0] != ("op", op):
            raise ValueError(f"{self.expr!r}: expected {op!r}")
        self.tokens.pop(0)


def _condition(expr, names: dict, status: dict) -> bool:
    """Whether an `if:` runs: absent is success(), and one without a status function is ANDed with it."""
    if isinstance(expr, bool):
        return status["success"] and expr
    expr = "success()" if expr is None else str(expr).strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    if not STATUS.search(expr):
        expr = f"success() && ({expr})"
    return bool(_Expression(expr, names, status).value())


def _why_skipped(expr, names: dict, status: dict) -> str | None:
    """None when the `if:` runs; else why: it is skipped, or the test cannot evaluate it."""
    try:
        return None if _condition(expr, names, status) else "is skipped"
    except ValueError as error:
        return f"has an if: the test cannot evaluate ({error})"


def _text(value) -> str:
    """An expression's value as GitHub writes it into an env variable."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _shell(job: dict, step: dict) -> list[str] | None:
    """How the step's script runs, None when not in bash. With no shell set, the runner decides: a
    runs-on written as an expression is any of its matrix's values, or unknown without one."""
    shell = step.get("shell") or job.get("defaults", {}).get("run", {}).get("shell")
    if shell is not None:
        return SHELLS.get(shell)
    runner = str(job.get("runs-on"))
    if EXPRESSION.search(runner):
        runner = str(job.get("strategy", {}).get("matrix") or "windows")
    return None if "windows" in runner.lower() else SHELLS["default"]


def _blocking_steps(jobs: dict):
    """(job name, job, step, kind) of each step of a gating job that fails the job when what it gates
    failed: "needs" reads a need's result; "command" pipes a command into tee or exits on its own;
    "action" uses an action that is the gate (GATING_ACTIONS)."""
    for name in sorted(_gating_jobs(jobs)):
        job = jobs[name]
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            if NEED_RESULT.search(str(step.get("env", "")) + run):
                yield name, job, step, "needs"
            elif "| tee" in run or EXIT.search(run):
                yield name, job, step, "command"
            elif str(step.get("uses", "")).startswith(GATING_ACTIONS):
                yield name, job, step, "action"


def _label(name: str, step: dict) -> str:
    return f"{name} / {step.get('name') or step.get('uses')}"


def _run_step(job: dict, step: dict, needs: dict[str, str], fail_call: int = 0) -> tuple[int, int]:
    """Run the step's script with each ${{ needs.X.result }} replaced from `needs` and any other
    expression by "x"; the commands it runs are the stubs, and the one called `fail_call`-th fails
    (0: none). Returns the exit code and how many stub calls were made."""
    def result(match):
        return needs.get(match[1].strip().removeprefix("needs.").removesuffix(".result"), "x")

    def fill(text):
        return EXPRESSION.sub(result, text)

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        stubs, venv = workdir / "stubs", workdir / ".venv" / "bin"
        for path in [stubs / tool for tool in STUBBED] + [venv / "python"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(STUB, encoding="utf-8")
            path.chmod(stat.S_IRWXU)
        script = workdir / "step.sh"
        script.write_text(fill(step["run"]), encoding="utf-8")
        env = {k: fill(str(v)) for k, v in step.get("env", {}).items()}
        calls = workdir / "stub-calls"
        calls.touch()
        env |= {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": tmp, "STUB_CALLS": str(calls), "STUB_FAIL": str(fail_call),
                "GITHUB_STEP_SUMMARY": str(workdir / "summary"), "RUNNER_TEMP": tmp, "GITHUB_WORKSPACE": tmp}
        shell = _shell(job, step)
        assert shell is not None, f"{step.get('name')}: no bash shell"
        code = subprocess.run([*shell, str(script)], cwd=workdir, env=env, capture_output=True, check=False).returncode
        return code, len(calls.read_text(encoding="utf-8").splitlines())


def _needs_checks(jobs: dict) -> list[str]:
    """Problems of the steps reading `needs.<n>.result`: all success must pass; each need failed,
    cancelled or skipped, one at a time, must fail, except skipped where SKIPPED_OK accepts it."""
    problems = []
    for name, job, step, kind in _blocking_steps(jobs):
        if kind != "needs":
            continue
        used = sorted(set(NEED_RESULT.findall(str(step.get("env", "")) + str(step.get("run", "")))))
        ok = dict.fromkeys(used, "success")
        if _run_step(job, step, ok)[0] != 0:
            problems.append(f"{_label(name, step)} fails when every need succeeded")
        for need in used:
            for result in ("failure", "cancelled", "skipped"):
                passes = _run_step(job, step, ok | {need: result})[0] == 0
                if passes != (result == "skipped" and need in SKIPPED_OK.get(name, set())):
                    problems.append(f"{_label(name, step)} {'passes' if passes else 'fails'} when {need} is {result}")
    return problems


def _command_checks(jobs: dict) -> list[str]:
    """Problems of the steps that pipe a command into tee or exit on their own: they pass when their
    commands succeed and fail when any one of them fails alone."""
    problems = []
    for name, job, step, kind in _blocking_steps(jobs):
        if kind != "command":
            continue
        if _shell(job, step) is None:
            problems.append(f"{_label(name, step)} runs without bash")
            continue
        code, calls = _run_step(job, step, {})
        if code != 0:
            problems.append(f"{_label(name, step)} fails when its commands succeed")
        elif calls == 0:
            problems.append(f"{_label(name, step)} runs no command the test can fail")
        problems += [f"{_label(name, step)} passes when its command {call} of {calls} fails"
                     for call in range(1, calls + 1) if code == 0 and _run_step(job, step, {}, call)[0] == 0]
    return problems


def _continue_checks(jobs: dict) -> list[str]:
    """A gating job, or a step of one, that continues on error passes when it failed."""
    problems = []
    for name in sorted(_gating_jobs(jobs)):
        if jobs[name].get("continue-on-error", False) is not False:
            problems.append(f"{name} continues on error")
        for step in jobs[name].get("steps", []):
            downloaded = step.get("with", {}).get("pattern") or step.get("with", {}).get("name")
            allowed = str(step.get("uses", "")).startswith("actions/download-artifact") and (name, downloaded) in CONTINUE_OK
            if step.get("continue-on-error", False) is not False and not allowed:
                problems.append(f"{_label(name, step)} continues on error")
    return problems


def _needs_if_checks(jobs: dict) -> list[str]:
    """A step reading needs.X.result that a condition can skip lets those needs fail unread."""
    return [f"{_label(name, step)} runs only if {step['if']!r}" for name, _, step, kind in _blocking_steps(jobs)
            if kind == "needs" and step.get("if") not in NEEDS_STEP_IFS]


def _job_env(job: dict, names: dict) -> dict[str, str]:
    """The job's env as its steps' `if:` reads it (env.X), each expression evaluated from `names`; a
    value the test cannot evaluate is left out, so an `if:` reading it cannot be evaluated either."""
    def fill(match):
        return _text(_Expression(match[1], names, RUNNING).value())

    env = {}
    for key, value in job.get("env", {}).items():
        try:
            env[f"env.{key}"] = EXPRESSION.sub(fill, str(value))
        except ValueError:
            continue
    return env


def _work_checks(jobs: dict) -> list[str]:
    """Each gating step runs when its needs succeeded with something to do (each output a target), on
    a PR and on a push: its `if:` evaluated with its job's env (mutation-ok's GATE) and the secrets of
    SECRETS_SET."""
    problems = []
    for name, job, step, _ in _blocking_steps(jobs):
        names = {f"needs.{n}.result": "success" for n in _needs(job)}
        names |= {f"needs.{n}.outputs.{o}": '["target"]' for n in _needs(job) for o in jobs[n].get("outputs", {})}
        for event in EVENTS:
            context = names | {"github.event_name": event} | {f"secrets.{secret}": "set" for secret in SECRETS_SET}
            context |= _job_env(job, context)
            if why := _why_skipped(step.get("if"), context, RUNNING):
                problems.append(f"{_label(name, step)} {why} on a {event} with work to gate")
    return problems


def _job_if_checks(jobs: dict) -> list[str]:
    """A required job runs when everything passed and when any one need failed: GitHub counts a
    skipped required job as a success (so one with needs keeps always() or !cancelled())."""
    problems = []
    for name in sorted(REQUIRED):
        job = jobs[name]
        for failed in [None, *_needs(job)]:
            for event in EVENTS:
                if why := _why_skipped(job.get("if"), *_scenario(job, failed, event)):
                    problems.append(f"{name} {why} on a {event} when {failed or 'nothing'} failed")
    return problems


def _scenario(job: dict, failed: str | None, event: str) -> tuple[dict, dict]:
    """The names and status functions of a job's `if:` on `event` when its need `failed` failed (None:
    every need succeeded)."""
    names = {f"needs.{n}.result": "failure" if n == failed else "success" for n in _needs(job)}
    return names | {"github.event_name": event}, RUNNING if failed is None else NEED_FAILED


def _unread_needs_checks(jobs: dict) -> list[str]:
    """A required job that runs when a need failed passes unless it reads that need's result. A need
    may go unread only when its failure still blocks the merge: it is required itself, or another
    required job reads its result (sonar needs coverage, which tests gates)."""
    gated = {n for r in REQUIRED for n in _needs(jobs[r]) if f"needs.{n}.result" in str(jobs[r].get("steps", []))}
    problems = []
    for name in sorted(REQUIRED):
        job = jobs[name]
        for need in _needs(job):
            if need in REQUIRED | gated or f"needs.{need}.result" in str(job.get("steps", [])):
                continue
            if any(_why_skipped(job.get("if"), *_scenario(job, need, event)) is None for event in EVENTS):
                problems.append(f"{name} passes even when {need} failed")
    return problems


DRAWING_STEP = "Diff the drawing against the base"
COMMENT_STEP = "Comment the CI report on the PR"
# What the drawing step runs, copied into a throwaway repo: diff.py imports modelo from scripts/.
DRAWING_SCRIPTS = ("__init__.py", "modelo.py", "diff.py")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="builds a throwaway git repo")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main",
                    *args], cwd=repo, check=True, capture_output=True)


def _drawing_run(job: dict, step: dict, base: str | None, pr: str, parent: bool = True) -> tuple[int, str | None]:
    """Run the drawing step in a throwaway repo checked out as the report job's is: HEAD the merge of
    a PR whose arquitetura/modelo.json is `pr` into a base whose is `base` (None: no file), or, without
    `parent`, one commit alone. The exit code and report/desenho.md (None when not written)."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        scripts = repo / "scripts" / "arquitetura"
        scripts.mkdir(parents=True)
        for name in DRAWING_SCRIPTS:
            shutil.copy(CI.parents[2] / "scripts" / "arquitetura" / name, scripts / name)
        (repo / "arquitetura").mkdir()
        model = repo / "arquitetura" / "modelo.json"
        _git(repo, "init", "-q")
        if parent:
            if base is not None:
                model.write_text(base, encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "base")
            _git(repo, "checkout", "-qb", "pr")
        model.write_text(pr, encoding="utf-8")
        (repo / "pr.txt").write_text("pr", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "pr")
        if parent:
            _git(repo, "checkout", "-q", "main")
            _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "pr")
        script = repo.parent / f"{repo.name}-step.sh"
        script.write_text(step["run"], encoding="utf-8")
        try:
            code = subprocess.run([*_shell(job, step), str(script)], cwd=repo, capture_output=True, check=False).returncode
        finally:
            script.unlink()
        out = repo / "report" / "desenho.md"
        return code, out.read_text(encoding="utf-8") if out.exists() else None


def _drawing_checks(jobs: dict) -> list[str]:
    """The report job's drawing diff (df-8x0m.23.38): run on a merge whose base lacks one arrow of the
    real model, on an unchanged one, on a base without the model, on a checkout without the first
    parent and on a PR model that is not JSON; the comment step runs after it whatever it did, with
    its outcome; and no other job holds a write token."""
    job = jobs["report"]
    step = _step(jobs, "report", DRAWING_STEP)
    if _shell(job, step) != SHELLS["bash"]:
        return [f"{_label('report', step)} runs without pipefail"]
    real = json.loads((CI.parents[2] / "arquitetura" / "modelo.json").read_text(encoding="utf-8"))
    pr = json.dumps(real)
    fewer = json.loads(pr)
    arrow = fewer["relations"].pop()
    problems = []
    code, out = _drawing_run(job, step, json.dumps(fewer), pr)
    if code != 0 or out is None or f"**Arrows**: 1 new\n- ➕ `{arrow['id']}`" not in out:
        problems.append(f"an added arrow: exit {code}, {out!r}")
    if _drawing_run(job, step, pr, pr) != (0, ""):
        problems.append("an unchanged drawing writes a comment or fails")
    code, out = _drawing_run(job, step, None, pr)
    if code != 0 or "everything is new" not in (out or ""):
        problems.append(f"a base without the model: exit {code}, {out!r}")
    if _drawing_run(job, step, None, pr, parent=False)[0] == 0:
        problems.append("a checkout without the base commit passes as a drawing all new")
    if _drawing_run(job, step, pr, "{not json")[0] == 0:
        problems.append("a failed diff passes")
    depth = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")).get("with", {}).get("fetch-depth")
    if not isinstance(depth, int) or depth < 2:
        problems.append(f"report checks out at depth {depth!r}: no base commit")
    comment = _step(jobs, "report", COMMENT_STEP)
    if _why_skipped(comment.get("if"), {}, STEP_FAILED) or not _why_skipped(comment.get("if"), {}, CANCELLED):
        problems.append(f"the comment step's if: {comment.get('if')!r} skips it after a failed diff or runs it on a cancelled run")
    if comment.get("env", {}).get("DESENHO") != f"${{{{ steps.{step.get('id')}.outcome }}}}":
        problems.append("the comment step does not get the drawing step's outcome")
    return problems


def _write_tokens(workflow: dict) -> list[str]:
    """Where the workflow grants a write token: "workflow" for its top-level permissions, else the jobs.
    A job without permissions gets the top level's, or the repo's default (read-only, set on GitHub)."""
    tops = ["workflow"] if "write" in str(workflow.get("permissions", "")) else []
    return tops + sorted(name for name, job in workflow["jobs"].items() if "write" in str(job.get("permissions", "")))


@pytest.mark.parametrize(("inject", "expected"), [
    (lambda w: None, ["report"]),
    (lambda w: w.update(permissions={"pull-requests": "write"}), ["workflow", "report"]),
    (lambda w: w.update(permissions="write-all"), ["workflow", "report"]),
    (lambda w: w["jobs"]["arquitetura"].update(permissions={"pull-requests": "write"}), ["arquitetura", "report"]),
], ids=["as-is", "top-level", "top-level-write-all", "another-job"])
def test_only_the_report_job_holds_a_write_token(inject, expected):
    """It posts the CI report and the drawing diff (df-8x0m.23.38): a second job with a write token is more surface."""
    workflow = yamlio.load(CI.read_text(encoding="utf-8"))
    inject(workflow)
    assert _write_tokens(workflow) == expected


CHECKS = [
    pytest.param(_needs_checks, marks=needs_bash, id="needs"),
    pytest.param(_command_checks, marks=needs_bash, id="commands"),
    pytest.param(_continue_checks, id="continue-on-error"),
    pytest.param(_needs_if_checks, id="needs-step-if"),
    pytest.param(_work_checks, id="step-if"),
    pytest.param(_job_if_checks, id="job-if"),
    pytest.param(_unread_needs_checks, id="unread-needs"),
    pytest.param(_drawing_checks, marks=[needs_bash, needs_git], id="drawing"),
]


@pytest.mark.parametrize("check", CHECKS)
def test_every_gating_step_and_job_blocks_when_what_it_gates_failed(check):
    assert check(_jobs()) == []


def test_the_checks_cover_the_steps_that_matter():
    jobs = _jobs()
    steps = {kind: {_label(n, s) for n, _, s, k in _blocking_steps(jobs) if k == kind} for kind in ("needs", "command", "action")}
    assert {"tests / Every part passed", "tests-windows / Every part passed", "mutation-ok / Check the mutation jobs"} <= steps["needs"]
    assert {"mutation-ok / Gate each target on its shards", "mutation / Mutate",
            "app-financas / Committed dist is the build", "tests-shard / Engine tests",
            "arquitetura / Validate the model", "arquitetura / The committed modelo.json is the export of the .c4",
            "arquitetura / Every mark of the target carries a bead"} <= steps["command"]
    assert steps["action"] == {"leak-scan / gitleaks", "sonar / SonarQube Cloud scan"}
    assert all(f"secrets.{secret}" in CI.read_text(encoding="utf-8") for secret in SECRETS_SET)
    downloads = {(n, s.get("with", {}).get("pattern") or s.get("with", {}).get("name"))
                 for n in _gating_jobs(jobs) for s in jobs[n].get("steps", []) if "continue-on-error" in s}
    assert downloads == set(CONTINUE_OK)
    assert all(set(needs) <= set(_needs(jobs[name])) for name, needs in SKIPPED_OK.items())


def test_the_arquitetura_job_validates_the_c4_folder_and_fails_on_a_warning():
    """`likec4 validate` of a folder with no .c4, or none at all, exits 0 with 'Valid (0 files)'
    (df-il2f); the folder is checked here, since a check in the step would also run on the stubs."""
    run = _step(_jobs(), "arquitetura", "Validate the model")["run"]
    folder = re.search(r" validate --max-warnings 0 (\S+) 2>&1", run)
    assert folder, run
    assert sorted((CI.parents[2] / folder[1]).glob("*.c4"))


def _step(jobs: dict, job: str, name: str) -> dict:
    return next(s for s in jobs[job]["steps"] if s.get("name") == name)


def _set(job: str, step: str | None, fields: dict):
    """A loophole: set `fields` on the job, or on its step named `step`; a None value drops the field."""
    def inject(jobs):
        target = jobs[job] if step is None else _step(jobs, job, step)
        for key, value in fields.items():
            if value is None:
                del target[key]
            else:
                target[key] = value
    return inject


def _replace(job: str, step: str, old: str, new: str):
    """A loophole: replace `old` by `new` in the run script of the job's step named `step`."""
    def inject(jobs):
        target = _step(jobs, job, step)
        assert old in target["run"], old
        target["run"] = target["run"].replace(old, new)
    return inject


def _not_cancelled_without_orders(jobs: dict):
    """A loophole: tests runs on !cancelled() and its "Every part passed" no longer reads orders."""
    jobs["tests"]["if"] = "!cancelled()"
    step = _step(jobs, "tests", "Every part passed")
    del step["env"]["ORDERS"]
    step["run"] = step["run"].replace(' && [ "$ORDERS" = "success" ]', "")


def _tee_after_a_command(jobs: dict):
    """A loophole: a step that runs a command, then pipes another into tee, in a shell without pipefail."""
    jobs["quality"]["steps"].append({"name": "Seeded", "run": "seed=$(python seed.py)\npytest -p \"$seed\" 2>&1 | tee out.txt\n"})


GATE = "Gate each target on its shards"
CHECK_MUTATION = "Check the mutation jobs"
LOOPHOLES = [
    # (1) continue-on-error on a step that gates, or on a gating job.
    ("coe-check-mutation-jobs", _continue_checks, _set("mutation-ok", CHECK_MUTATION, {"continue-on-error": True})),
    ("coe-gate", _continue_checks, _set("mutation-ok", GATE, {"continue-on-error": True})),
    ("coe-engine-tests", _continue_checks, _set("tests-shard", "Engine tests", {"continue-on-error": True})),
    ("coe-mutate", _continue_checks, _set("mutation", "Mutate", {"continue-on-error": True})),
    ("coe-job-mutation-ok", _continue_checks, _set("mutation-ok", None, {"continue-on-error": True})),
    ("coe-job-mutation", _continue_checks, _set("mutation", None, {"continue-on-error": True})),
    ("coe-job-tests", _continue_checks, _set("tests", None, {"continue-on-error": True})),
    # (2) a condition on a step that reads needs.X.result.
    ("if-false-check-mutation-jobs", _needs_if_checks, _set("mutation-ok", CHECK_MUTATION, {"if": False})),
    ("if-every-part-passed", _needs_if_checks,
     _set("tests", "Every part passed", {"if": "needs.tests-shard.result == 'success'"})),
    # (3) mutation-ok's GATE inverted, or its gate step's if swapped; any gating step skipped.
    ("gate-env-inverted", _work_checks, _set("mutation-ok", None, {"env": {
        "GATE": "${{ needs.mutation-targets.result == 'success' && needs.mutation-targets.outputs.targets == '[]' }}"}})),
    ("gate-if-swapped", _work_checks, _set("mutation-ok", GATE, {"if": "env.GATE == 'false'"})),
    ("engine-tests-if-false", _work_checks, _set("tests-shard", "Engine tests", {"if": False})),
    ("check-mutation-if-false", _work_checks, _set("mutation-ok", CHECK_MUTATION, {"if": False})),
    # (4) a required job with needs that loses always().
    ("tests-without-always", _job_if_checks, _set("tests", None, {"if": None})),
    ("mutation-ok-on-success", _job_if_checks, _set("mutation-ok", None, {"if": "success()"})),
    ("sonar-only-after-coverage", _job_if_checks,
     _set("sonar", None, {"if": "!cancelled() && needs.coverage.result == 'success'"})),
    ("tests-on-not-cancelled-orders-unread", _unread_needs_checks, _not_cancelled_without_orders),
    # A name the test gives no value for: the step or job may be skipped on the PRs it names.
    ("gate-not-for-a-bot", _work_checks, _set("mutation-ok", GATE, {"if": "env.GATE == 'true' && github.actor != 'renovate[bot]'"})),
    ("gate-not-for-a-branch", _work_checks, _set("mutation-ok", GATE, {"if": "env.GATE == 'true' && github.head_ref != 'renovate/lock'"})),
    ("gate-not-on-a-draft", _work_checks, _set("mutation-ok", GATE, {"if": "env.GATE == 'true' && !github.event.pull_request.draft"})),
    ("tests-not-for-a-bot", _job_if_checks, _set("tests", None, {"if": "always() && github.actor != 'renovate[bot]'"})),
    ("quality-not-for-a-bot", _job_if_checks, _set("quality", None, {"if": "github.actor != 'renovate[bot]'"})),
    # (5) a non-required job's step that gates by a plain exit.
    ("mutate-swallows-a-failure", _command_checks, _replace("mutation", "Mutate", "|| failed=1", "|| true")),
    ("committed-dist-exits-0", _command_checks, _replace("app-financas", "Committed dist is the build", "exit 1", "exit 0")),
    ("stale-model-passes", _command_checks, _set("arquitetura", "The committed modelo.json is the export of the .c4", {"shell": None})),
    ("unmarked-change-passes", _command_checks, _set("arquitetura", "Every mark of the target carries a bead", {"shell": None})),
    ("validate-swallows-a-failure", _command_checks, _replace("arquitetura", "Validate the model", "arquitetura 2>&1", "arquitetura || true; true 2>&1")),
    ("tests-ignores-arquitetura", _needs_checks, _replace("tests", "Every part passed", ' && [ "$ARQUITETURA" = "success" ]', "")),
    # skipped, accepted only where SKIPPED_OK says.
    ("tests-accepts-skipped-shards", _needs_checks, _replace("tests", "Every part passed", '[ "$SHARDS" = "success" ]',
                                                              '{ [ "$SHARDS" = "success" ] || [ "$SHARDS" = "skipped" ]; }')),
    ("mutation-ok-refuses-skipped", _needs_checks, _replace("mutation-ok", CHECK_MUTATION, ' || [ "$MUTATION" = "skipped" ]', "")),
    # The bugs df-g2ot injected.
    ("pick-not-gated", _needs_checks,
     _replace("mutation-ok", CHECK_MUTATION, '[ "$PICK" = "success" ] || exit 1', '[ "$PICK" = "success" ] || echo')),
    ("mutation-not-gated", _needs_checks,
     _replace("mutation-ok", CHECK_MUTATION, '[ "$MUTATION" = "skipped" ] || exit 1', '[ "$MUTATION" = "skipped" ] || echo')),
    ("tee-without-pipefail", _command_checks, _set("mutation-ok", GATE, {"shell": None})),
    # df-qn1u.1: a command that fails under -e before the pipe no longer hides the lost pipefail.
    ("tee-without-pipefail-after-a-command", _command_checks, _tee_after_a_command),
    # df-5tn0: a `uses:` step that is the gate, skipped.
    ("gitleaks-if-false", _work_checks, _set("leak-scan", "gitleaks", {"if": False})),
    ("sonar-scan-if-false", _work_checks, _set("sonar", "SonarQube Cloud scan", {"if": False})),
    ("sonar-scan-without-a-token", _work_checks, _set("sonar", "SonarQube Cloud scan", {"if": "env.SONAR_TOKEN == ''"})),
    ("sonar-scan-not-on-a-pr", _work_checks, _set("sonar", "SonarQube Cloud scan", {"if": "github.event_name != 'pull_request'"})),
    # df-8x0m.23.38: the drawing diff of the report job.
    ("drawing-without-the-parent-check", _drawing_checks, _replace("report", DRAWING_STEP, "git rev-parse --verify --quiet 'HEAD^1' > /dev/null\n", "")),
    ("drawing-swallows-a-failure", _drawing_checks, _replace("report", DRAWING_STEP, "> report/desenho.md", "> report/desenho.md || true")),
    ("drawing-without-pipefail", _drawing_checks, _set("report", DRAWING_STEP, {"shell": None})),
    ("drawing-at-depth-1", _drawing_checks, lambda jobs: jobs["report"]["steps"][0].pop("with")),
    ("drawing-comment-skipped-after-a-failed-diff", _drawing_checks, _set("report", COMMENT_STEP, {"if": None})),
    ("drawing-outcome-not-passed", _drawing_checks, lambda jobs: _step(jobs, "report", COMMENT_STEP)["env"].pop("DESENHO")),
    ("drawing-comment-on-a-cancelled-run", _drawing_checks, _set("report", COMMENT_STEP, {"if": "always()"})),
]


@pytest.mark.parametrize(
    ("check", "inject"),
    [pytest.param(check, inject, id=name, marks=[needs_bash, needs_git] if check in (_needs_checks, _command_checks, _drawing_checks) else [])
     for name, check, inject in LOOPHOLES],
)
def test_the_checks_catch_each_loophole(check, inject):
    jobs = _jobs()
    inject(jobs)
    assert check(jobs) != []


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("success() || failure()", True),
        ("failure() || success()", True),
        ("github.event_name == 'pull_request' || github.event_name == 'push'", True),
        ("success() && (github.event_name == 'push' || github.event_name == 'pull_request')", True),
        ("github.event_name != 'pull_request' && always()", False),
        ("!(env.GATE == 'TRUE') || cancelled()", False),
        ("env.GATE && 'x'", "x"),
        ("'' || env.GATE", "true"),
        ("'it''s' == 'it''s'", True),
    ],
)
def test_an_expression_evaluates_as_github_does(expr, expected):
    names = {"github.event_name": "pull_request", "env.GATE": "true"}
    assert _Expression(expr, names, RUNNING).value() == expected


@pytest.mark.parametrize(
    "expr",
    ["github.actor != 'renovate[bot]'", "!github.event.pull_request.draft", "null == ''", "env.GATE == true",
     "always(", "success() success()", "env.GATE == 'true' &&", "hashFiles('x')"],
)
def test_an_expression_the_test_cannot_evaluate_fails_loudly(expr):
    names = {"github.event_name": "pull_request", "env.GATE": "true"}
    with pytest.raises(ValueError):
        _condition(expr, names, RUNNING)


@pytest.mark.parametrize(
    ("workflow_shell", "job", "step", "expected"),
    [
        (None, {"runs-on": "ubuntu-latest"}, {}, SHELLS["default"]),
        ("bash", {"runs-on": "ubuntu-latest"}, {}, SHELLS["bash"]),
        ("bash", {"runs-on": "windows-latest"}, {}, SHELLS["bash"]),
        ("bash", {"runs-on": "ubuntu-latest", "defaults": {"run": {"shell": "pwsh"}}}, {}, None),
        ("pwsh", {"runs-on": "ubuntu-latest"}, {"shell": "bash"}, SHELLS["bash"]),
        ("pwsh", {"runs-on": "ubuntu-latest"}, {}, None),
        (None, {"runs-on": "windows-latest"}, {}, None),
        (None, {"runs-on": "Windows-2025"}, {}, None),
        (None, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["ubuntu-latest", "windows-latest"]}}}, {}, None),
        (None, {"runs-on": "${{ matrix.os }}", "strategy": {"matrix": {"os": ["ubuntu-latest", "ubuntu-24.04"]}}}, {}, SHELLS["default"]),
        (None, {"runs-on": "${{ inputs.runner }}"}, {}, None),
    ],
)
def test_a_step_runs_in_the_shell_its_step_job_workflow_or_runner_gives(workflow_shell, job, step, expected):
    workflow = {"jobs": {"job": job}} | ({"defaults": {"run": {"shell": workflow_shell}}} if workflow_shell else {})
    assert _shell(_with_workflow_defaults(workflow)["job"], step) == expected
