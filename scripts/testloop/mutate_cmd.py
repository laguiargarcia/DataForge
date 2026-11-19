"""`mutate`: mutation testing of one module with mutmut, run on a throwaway copy.

This is the only file that knows the mutation tool. Everything the tool needs
(its config, its `mutants/` directory) lives in the temporary copy.

`--shard I/N` mutates only the I-th of N parts of the module (df-n8ac.32): its top-level functions
and methods, split by line count, so N CI jobs share a big target and `gate` sums them.

`--cache-dir DIR` (df-n8ac.38) keeps mutmut's verdicts between runs, and a run reuses only the
killed ones: it tests again every mutant that survived, timed out or had no test (a test added since
may kill it), and those of functions whose text changed, comments included (every one when the pytest
arguments or the list of test files change). A killed verdict stays through any change outside its
function: a changed or weakened test, the module's constants or imports, a function it calls (in this
module or another), a base class, the files in `copy`, conftest.py, `env` in testloop.toml. The full
run of main, which starts from no cache, audits those (df-n8ac.35).
"""

import ast
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from common import (
    Config, ToolError, mutation_score, pytest_args, resolve_tests, result_path, shard_count, test_env, write_result,
)

TOOL = "mutmut"
DEFAULT_BUDGET_SECONDS = 900
# A run longer than this keeps the PR waiting: the target should be split into more shards.
SLOW_SECONDS = 240
WHOLE = (1, 1)

# mutmut 3's names (mutmut/utils/format_utils.py): a top-level function's mutants are
# <module>.x_<name>__mutmut_<n>, a method's <module>.xǁ<Class>ǁ<name>__mutmut_<n>.
CLASS_SEPARATOR = "ǁ"
MUTANT_INFIX = "__mutmut_"
# What `mutmut run <names>` says (an assert) when no mutant matches the names.
NOTHING_MATCHES = "Filtered for specific mutants, but nothing matches"
# A name no mutant has: `mutmut run PROBE` writes the mutants and merges the cached verdicts, then
# stops at NOTHING_MATCHES before testing any (df-n8ac.38).
PROBE = "testloop-cache-probe"

# What the cache keeps of mutmut's mutants/ (df-n8ac.38): its stats (which tests reach which
# function, their durations) and a .meta per mutated file (each mutant's verdict, each function's
# hash). Both name files relative to the copy, so they move to a new copy. The mutant sources are left
# out: mutmut writes them anew, and one newer than its source would make mutmut skip the hash check.
MUTANTS = "mutants"
STATS_FILE = "mutmut-stats.json"
META_SUFFIX = ".meta"
# Beside them, the hash of each mutated function's text when its verdicts were found. mutmut keeps a
# verdict by mutant name while the function's AST hash holds, and that hash ignores comments: a
# `# pragma: no mutate` renumbers a function's mutants under the same hash, and a mutant would take
# the verdict of the one that had its number. A function whose text differs loses its verdicts first.
TEXTS_FILE = "testloop-function-text.json"

# mutmut statuses we know how to classify.
KILLED, SURVIVED = "killed", "survived"
TIMEOUT = "timeout"
NOT_TESTED = ("no tests", "not checked")


class BudgetExceeded(Exception):
    pass


def state_files(mutants: Path) -> list[Path]:
    """The files of mutmut's state under `mutants`, relative to it."""
    if not mutants.is_dir():
        return []
    return sorted(
        p.relative_to(mutants) for p in mutants.rglob("*")
        if p.is_file() and (p.name == STATS_FILE or p.name.endswith(META_SUFFIX))
    )


def _copy_state(src: Path, dest: Path) -> int:
    files = state_files(src)
    for rel in files:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dest / rel)
    return len(files)


def _forget_changed(meta: Path, cached: dict[str, str], texts: dict[str, str]) -> None:
    """Drop from a restored .meta the verdicts of each function whose text is not the cached one."""
    data = json.loads(meta.read_text(encoding="utf-8"))
    for key in data["exit_code_by_key"]:
        function = function_of(key)
        if texts.get(function) is None or cached.get(function) != texts[function]:
            data["exit_code_by_key"][key] = None
            data.get("durations_by_key", {}).pop(key, None)
            data.get("type_check_error_by_key", {}).pop(key, None)
    meta.write_text(json.dumps(data, indent=4), encoding="utf-8")


def restore_cache(cache_dir: Path, copy: Path, module: str, texts: dict[str, str]) -> bool:
    """Put the cached state into the copy's mutants/, without the verdicts of functions whose text
    changed (`texts`: function_texts of the module now); False when the cache holds none."""
    if _copy_state(cache_dir / MUTANTS, copy / MUTANTS) == 0:
        return False
    meta = copy / MUTANTS / (module + META_SUFFIX)
    if meta.is_file():
        try:
            cached = json.loads((cache_dir / MUTANTS / TEXTS_FILE).read_text(encoding="utf-8")).get(module, {})
        except FileNotFoundError:
            cached = {}
        _forget_changed(meta, cached, texts)
    return True


def save_cache(copy: Path, cache_dir: Path, module: str, texts: dict[str, str]) -> None:
    """Replace the cache with the copy's state and the module's function texts: written beside it,
    then renamed into place, so a run cut short leaves the old cache whole."""
    current, new, old = (cache_dir / name for name in (MUTANTS, MUTANTS + ".new", MUTANTS + ".old"))
    for leftover in (new, old):
        shutil.rmtree(leftover, ignore_errors=True)
    new.mkdir(parents=True)
    _copy_state(copy / MUTANTS, new)
    (new / TEXTS_FILE).write_text(json.dumps({module: texts}, indent=2, sort_keys=True), encoding="utf-8")
    if current.exists():
        current.rename(old)
    new.rename(current)
    shutil.rmtree(old, ignore_errors=True)


def _functions(source: str):
    """mutmut's key of each function it can mutate (top-level functions and the methods of
    top-level classes) with its node; a name defined twice comes twice."""
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.parse(source).body:
        if isinstance(node, functions):
            yield f"x_{node.name}", node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, functions):
                    yield f"x{CLASS_SEPARATOR}{node.name}{CLASS_SEPARATOR}{item.name}", item


def function_weights(source: str) -> dict[str, int]:
    """Each function key, weighted by its number of lines."""
    weights: dict[str, int] = {}
    for key, node in _functions(source):
        weights[key] = weights.get(key, 0) + node.end_lineno - node.lineno + 1
    return weights


def function_texts(source: str) -> dict[str, str]:
    """Each function key with a hash of its lines, decorators and comments included."""
    lines = source.splitlines(keepends=True)
    texts: dict[str, str] = {}
    for key, node in _functions(source):
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        texts[key] = texts.get(key, "") + "".join(lines[start - 1:node.end_lineno])
    return {key: "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() for key, text in texts.items()}


def partition(weights: dict[str, int], n: int) -> list[list[str]]:
    """Split the keys in n shards of near-equal weight: heaviest first (ties by name), each to
    the lightest shard so far (ties to the lowest index). Every key lands in exactly one shard."""
    shards: list[list[str]] = [[] for _ in range(n)]
    loads = [0] * n
    for key in sorted(weights, key=lambda k: (-weights[k], k)):
        lightest = min(range(n), key=lambda i: (loads[i], i))
        shards[lightest].append(key)
        loads[lightest] += weights[key]
    return [sorted(shard) for shard in shards]


def _mutant_module(module: str) -> str:
    """The dotted name mutmut puts before a key: a package's __init__ drops out."""
    return module.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def shard_globs(module: str, keys: list[str]) -> list[str]:
    """`mutmut run` filters: every mutant of these functions."""
    return [f"{_mutant_module(module)}.{key}{MUTANT_INFIX}*" for key in keys]


def function_of(mutant: str) -> str:
    """The function key a mutant belongs to: m.x_f__mutmut_3 -> x_f. The suffix is the last
    infix: a function named _mutmut_version has key x__mutmut_version."""
    return mutant.rpartition(MUTANT_INFIX)[0].rpartition(".")[2]


def slow_warning(module: str, elapsed: float) -> str | None:
    if elapsed <= SLOW_SECONDS:
        return None
    return (f"mutate took {elapsed:.0f}s, over {SLOW_SECONDS}s: raise `shards` for {module} in "
            f"testloop.toml so its CI jobs stay short")


def _shard_keys(config: Config, module: str, shard: tuple[int, int]) -> set[str] | None:
    """The function keys of the shard; None for the whole module, which is always allowed."""
    if shard == WHOLE:
        return None
    shards = shard_count(config, module)
    if shard[1] != shards:
        raise ToolError(f"{module} has shards = {shards} in testloop.toml; --shard {shard[0]}/{shard[1]} "
                        "would write a result its gate never reads")
    try:
        weights = function_weights((config.repo / module).read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise ToolError(f"{module} does not parse, so its functions cannot be split: {exc}") from exc
    return set(partition(weights, shard[1])[shard[0] - 1])


def _in_shard(results: dict[str, str], keys: set[str] | None) -> dict[str, str]:
    """The results of the shard's functions; mutmut lists every mutant, run or not."""
    if keys is None:
        return results
    return {k: v for k, v in results.items() if function_of(k) in keys}


def _tail(text: str, n: int = 1500) -> str:
    return text[-n:].strip()


def _tmp_root() -> str | None:
    """A temp dir outside the Windows mount (slow and flaky for process-heavy runs)."""
    return "/tmp" if tempfile.gettempdir().startswith("/mnt/") else None


def _python(config: Config) -> str:
    """Absolute interpreter path: the copy runs with a different working directory."""
    if os.sep in config.python and not os.path.isabs(config.python):
        return str(config.repo / config.python)
    return config.python


def _run(cmd: list[str], cwd: Path, env: dict, deadline: float) -> tuple[int, str]:
    """Run in its own process group; kill the whole group if the deadline passes."""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
        try:
            proc.wait(timeout=max(deadline - time.monotonic(), 0.001))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise BudgetExceeded() from None
        finally:
            # also reap stragglers left in the group by a normal exit
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        out.seek(0)
        return proc.returncode, out.read()


def _copy_files(config: Config, module: str, tests: list[str], dest: Path) -> None:
    target = config.targets.get(module, {})
    rels = {module, *tests}
    for pattern in target.get("copy", []):
        rels.update(
            p.relative_to(config.repo).as_posix()
            for p in config.repo.glob(pattern) if p.is_file()
        )
    for rel in sorted(rels):
        src = config.repo / rel
        if "__pycache__" in src.parts or not src.is_file():
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest / rel)


def _write_tool_config(config: Config, module: str, tests: list[str], dest: Path) -> None:
    parts = Path(module).parts
    source = parts[0] if len(parts) > 1 else module
    lines = [
        "[tool.mutmut]",
        f"source_paths = {json.dumps([source])}",
        f"only_mutate = {json.dumps([module])}",
        f"pytest_add_cli_args = {json.dumps(pytest_args(config))}",
        f"pytest_add_cli_args_test_selection = {json.dumps(tests)}",
    ]
    # mutmut's default ("fork") runs the clean suite in its main process, then forks each mutant from
    # it: a thread pool a test started there (polars, in the leak guard's tests) is gone in the child,
    # which waits for it until the timeout (df-kmx0). A target whose tests do that sets
    # process_isolation = "forkserver" (the server only collects); the others keep "fork", whose
    # children inherit a warmed-up process and run 1.5-4x faster (df-3lgk).
    isolation = config.targets.get(module, {}).get("process_isolation")
    if isolation:
        lines.append(f"process_isolation = {json.dumps(isolation)}")
    (dest / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_results(text: str) -> dict[str, str]:
    results = {}
    for line in text.splitlines():
        line = line.strip()
        if ": " in line and "__mutmut_" in line.split(": ", 1)[0]:
            key, status = line.split(": ", 1)
            results[key.strip()] = status.strip()
    return results


def _diff(python: str, copy: Path, env: dict, key: str, deadline: float) -> str:
    code, out = _run([python, "-m", "mutmut", "show", key], copy, env, deadline)
    if code != 0:
        raise ToolError(f"{TOOL} show {key} failed:\n{_tail(out)}")
    lines = out.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _collect(python: str, copy: Path, env: dict, deadline: float) -> dict[str, str]:
    code, out = _run([python, "-m", "mutmut", "results", "--all", "true"], copy, env, deadline)
    if code != 0:
        raise ToolError(f"{TOOL} results failed:\n{_tail(out)}")
    return _parse_results(out)


def _summarise(results: dict[str, str]) -> dict:
    killed = 0
    timeout, not_tested, survivors, unknown = [], [], [], {}
    for key, status in sorted(results.items()):
        if status == KILLED:
            killed += 1
        elif status == SURVIVED:
            survivors.append(key)
        elif status == TIMEOUT:
            timeout.append(key)
        elif status in NOT_TESTED:
            not_tested.append(key)
        else:
            unknown[key] = status  # never counted as killed
    return {
        "killed": killed, "survivors": survivors, "timeout": timeout,
        "not_tested": not_tested, "unknown": unknown,
    }


def _result(summary: dict, diffs: dict[str, str], total: int, complete: bool,
            elapsed: float, warnings: list[str], tests: list[str], version: str) -> dict:
    return {
        "cache": "none",
        "cached": 0,
        "tests": tests,
        "tool": {"name": TOOL, "version": version},
        "complete": complete,
        "total": total,
        "killed": summary["killed"],
        "survived": [{"id": k, "diff": diffs.get(k, "")} for k in summary["survivors"]],
        "timeout": summary["timeout"],
        "not_tested": summary["not_tested"],
        "score": mutation_score(summary["killed"], len(summary["survivors"]), len(summary["not_tested"])),
        "elapsed_s": round(elapsed, 2),
        "warnings": warnings,
    }


def _mutmut_version() -> str:
    try:
        from importlib.metadata import version

        return version(TOOL)
    except Exception:
        return "unknown"


@dataclass
class _Mutation:
    """One mutate run: what to mutate, where the copy is, and which shard of the module."""
    config: Config
    module: str
    tests: list[str]
    python: str
    copy: Path
    env: dict
    started: float
    shard: tuple[int, int]
    keys: set[str] | None   # the shard's function keys; None = the whole module
    restored: bool = False  # the copy started from a cache's verdicts
    cached: int = 0         # mutants of the shard whose killed verdict came from the cache

    def write(self, summary: dict, diffs: dict[str, str], total: int, complete: bool,
              warnings: list[str]) -> Path:
        elapsed = time.monotonic() - self.started
        if slow := slow_warning(self.module, elapsed):
            warnings.append(slow)
            print(f"testloop: {slow}", file=sys.stderr)
        data = _result(summary, diffs, total, complete, elapsed, warnings, self.tests, _mutmut_version())
        if self.restored:
            data.update(cache="restored", cached=self.cached)
        return write_result(self.config.repo, "mutate", self.module, data, shard=self.shard)


def main(config: Config, module: str, budget_seconds: float = DEFAULT_BUDGET_SECONDS,
         shard: tuple[int, int] | None = None, cache_dir: Path | None = None) -> tuple[Path, int]:
    if not (config.repo / module).is_file():
        raise ToolError(f"module not found: {module}")
    shard = shard or WHOLE
    tests = resolve_tests(config, module)
    started = time.monotonic()
    deadline = started + budget_seconds
    env = test_env(config, PYTHONDONTWRITEBYTECODE="1")
    env.pop("MUTANT_UNDER_TEST", None)

    keys = _shard_keys(config, module, shard)   # before the copy: a refusal leaves no temp dir
    copy = Path(tempfile.mkdtemp(prefix="testloop-mutate-", dir=_tmp_root()))
    job = _Mutation(config, module, tests, _python(config), copy, env, started, shard, keys)
    try:
        _copy_files(config, module, tests, copy)
        _write_tool_config(config, module, tests, copy)
        texts: dict[str, str] = {}
        if cache_dir is not None:
            cache_dir = Path(cache_dir).resolve()
            texts = _module_texts(config, module)
            job.restored = restore_cache(cache_dir, copy, module, texts)
        try:
            out_path, code = _mutate(job, deadline)
        except BudgetExceeded:
            return _partial(job, budget_seconds)
        if cache_dir is not None and code == 0:   # only a complete run's verdicts go back
            save_cache(copy, cache_dir, module, texts)
        return out_path, code
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def _module_texts(config: Config, module: str) -> dict[str, str]:
    try:
        return function_texts((config.repo / module).read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise ToolError(f"{module} does not parse, so its cached verdicts cannot be checked: {exc}") from exc


def _mutants(job: _Mutation, deadline: float) -> dict[str, str]:
    """Run mutmut on the whole module, or only on the mutants of the shard's functions, and
    return the status of each mutant it counts. A shard can have no function at all (more shards
    than functions) or only functions without mutants, which mutmut refuses to filter for: both
    are a complete result of 0 mutants."""
    python, copy, env, module = job.python, job.copy, job.env, job.module
    if job.keys is not None and not job.keys:
        return {}
    names = [] if job.keys is None else shard_globs(module, sorted(job.keys))
    out = ""   # mutmut's output; none when a restored cache leaves nothing to test
    if job.restored:
        names = _pending(job, deadline)
    if names or not job.restored:
        code, out = _run([python, "-m", "mutmut", "run", *names], copy, env, deadline)
        if code != 0 and not (names and NOTHING_MATCHES in out):
            raise ToolError(f"{TOOL} run exited {code}:\n{_tail(out)}")
    everything = _collect(python, copy, env, deadline)
    if job.keys is None:
        if not everything:
            raise ToolError(f"{TOOL} generated no mutants for {module}:\n{_tail(out)}")
        return everything
    known = function_weights((job.config.repo / module).read_text(encoding="utf-8"))
    strays = sorted(k for k in everything if function_of(k) not in known)
    if strays:
        raise ToolError(f"{TOOL} mutants outside every shard (the split misses their function): {strays[:5]}")
    return _in_shard(everything, job.keys)


def _pending(job: _Mutation, deadline: float) -> list[str]:
    """With a restored cache: write the mutants and let mutmut merge the cached verdicts (it drops
    those of functions whose hash changed, and all of them when the pytest arguments or the list of
    test files changed), then return the shard's mutants to test: every one not killed, so a survivor a
    new test kills is reported killed. Named one by one, because `mutmut run` tests again every mutant
    a name or glob matches, verdict or not."""
    python, copy, env = job.python, job.copy, job.env
    code, out = _run([python, "-m", "mutmut", "run", PROBE], copy, env, deadline)
    if NOTHING_MATCHES not in out:
        raise ToolError(f"{TOOL} run {PROBE} (writing the mutants) exited {code}:\n{_tail(out)}")
    shard = _in_shard(_collect(python, copy, env, deadline), job.keys)
    pending = sorted(k for k, status in shard.items() if status != KILLED)
    job.cached = len(shard) - len(pending)
    return pending


def _mutate(job: _Mutation, deadline: float) -> tuple[Path, int]:
    python, copy, env, module = job.python, job.copy, job.env, job.module

    # 1. The code under test must be the copy, not an editable install of the repo.
    dotted = module.removesuffix(".py").replace("/", ".")
    probe = (
        "import importlib.util, sys; s = importlib.util.find_spec(sys.argv[1]); "
        "print(s.origin if s else '')"
    )
    code, out = _run([python, "-c", probe, dotted], copy, env, deadline)
    origin = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0 or not origin or Path(origin).resolve().parent != (copy / module).resolve().parent:
        raise ToolError(f"{dotted} would not be imported from the temporary copy (got {origin or out.strip()!r})")

    # 2. Baseline: the copy's tests must pass before anything is mutated.
    code, out = _run(
        [python, "-m", "pytest", *pytest_args(job.config), *job.tests],
        copy, env, deadline,
    )
    if code != 0:
        raise ToolError(f"baseline tests fail in the copy (pytest exit {code}); not mutating:\n{_tail(out)}")

    # 3. Mutate.
    results = _mutants(job, deadline)
    summary = _summarise(results)
    if summary["unknown"]:
        shown = ", ".join(f"{k} ({v})" for k, v in list(summary["unknown"].items())[:5])
        raise ToolError(f"unclassified {TOOL} statuses (not counted as killed): {shown}")

    diffs = {k: _diff(python, copy, env, k, deadline) for k in summary["survivors"]}
    warnings = []
    complete = not any(results[k] == "not checked" for k in results)
    if not complete:
        warnings.append("some mutants were never checked; result is partial")
    if results and summary["killed"] == 0:
        warnings.append("no mutant was killed: the suite may be genuinely weak, or the run is not measuring it")
    out_path = job.write(summary, diffs, len(results), complete, warnings)
    return out_path, (0 if complete else 2)


def _partial(job: _Mutation, budget_seconds: float) -> tuple[Path, int]:
    """Budget exhausted: the process group is already dead. Record what we have."""
    python, copy, env = job.python, job.copy, job.env
    warnings = [f"budget of {budget_seconds}s exhausted; result is partial"]
    results: dict[str, str] = {}
    try:
        results = _in_shard(_collect(python, copy, env, time.monotonic() + 30), job.keys)
    except (ToolError, BudgetExceeded):
        warnings.append("could not read partial results")
    summary = _summarise(results)
    # partial: every mutant without a final verdict is "not tested"; unknown never counts as killed
    summary["not_tested"] = sorted(set(summary["not_tested"]) | set(summary["unknown"]))
    for key, status in summary["unknown"].items():
        warnings.append(f"{key}: unclassified status {status!r}")
    diffs = {}
    for key in summary["survivors"]:
        try:
            diffs[key] = _diff(python, copy, env, key, time.monotonic() + 30)
        except (ToolError, BudgetExceeded):
            pass
    job.write(summary, diffs, len(results), False, warnings)
    print(f"testloop: mutate budget of {budget_seconds}s exhausted; partial result written (complete: false)",
          file=sys.stderr)
    # Returns (path, 2): the CLI prints the path and exits 2, and the partial result is
    # marked complete: false.
    return result_path(job.config.repo, "mutate", job.module, job.shard), 2
