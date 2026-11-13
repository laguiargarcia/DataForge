"""`mutate`: mutation testing of one module with mutmut, run on a throwaway copy.

This is the only file that knows the mutation tool. Everything the tool needs
(its config, its `mutants/` directory) lives in the temporary copy.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common import Config, ToolError, pytest_args, resolve_tests, test_env, write_result

TOOL = "mutmut"
DEFAULT_BUDGET_SECONDS = 900

# mutmut statuses we know how to classify.
KILLED, SURVIVED = "killed", "survived"
TIMEOUT = "timeout"
NOT_TESTED = ("no tests", "not checked")


class BudgetExceeded(Exception):
    pass


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
    import json

    parts = Path(module).parts
    source = parts[0] if len(parts) > 1 else module
    lines = [
        "[tool.mutmut]",
        f"source_paths = {json.dumps([source])}",
        f"only_mutate = {json.dumps([module])}",
        f"pytest_add_cli_args = {json.dumps(pytest_args(config))}",
        f"pytest_add_cli_args_test_selection = {json.dumps(tests)}",
    ]
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
    denom = summary["killed"] + len(summary["survivors"]) + len(summary["not_tested"])
    return {
        "tests": tests,
        "tool": {"name": TOOL, "version": version},
        "complete": complete,
        "total": total,
        "killed": summary["killed"],
        "survived": [{"id": k, "diff": diffs.get(k, "")} for k in summary["survivors"]],
        "timeout": summary["timeout"],
        "not_tested": summary["not_tested"],
        "score": (summary["killed"] / denom) if denom else None,
        "elapsed_s": round(elapsed, 2),
        "warnings": warnings,
    }


def _mutmut_version() -> str:
    try:
        from importlib.metadata import version

        return version(TOOL)
    except Exception:
        return "unknown"


def main(config: Config, module: str, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> tuple[Path, int]:
    if not (config.repo / module).is_file():
        raise ToolError(f"module not found: {module}")
    tests = resolve_tests(config, module)
    python = _python(config)
    started = time.monotonic()
    deadline = started + budget_seconds
    env = test_env(config, PYTHONDONTWRITEBYTECODE="1")
    env.pop("MUTANT_UNDER_TEST", None)

    copy = Path(tempfile.mkdtemp(prefix="testloop-mutate-", dir=_tmp_root()))
    try:
        _copy_files(config, module, tests, copy)
        _write_tool_config(config, module, tests, copy)
        try:
            return _mutate(config, module, tests, python, copy, env, started, deadline)
        except BudgetExceeded:
            return _partial(config, module, tests, python, copy, env, started, budget_seconds)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def _mutate(config, module, tests, python, copy, env, started, deadline):
    version = _mutmut_version()

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
        [python, "-m", "pytest", *pytest_args(config), *tests],
        copy, env, deadline,
    )
    if code != 0:
        raise ToolError(f"baseline tests fail in the copy (pytest exit {code}); not mutating:\n{_tail(out)}")

    # 3. Mutate.
    code, out = _run([python, "-m", "mutmut", "run"], copy, env, deadline)
    if code != 0:
        raise ToolError(f"{TOOL} run exited {code}:\n{_tail(out)}")

    results = _collect(python, copy, env, deadline)
    if not results:
        raise ToolError(f"{TOOL} generated no mutants for {module}:\n{_tail(out)}")
    summary = _summarise(results)
    if summary["unknown"]:
        shown = ", ".join(f"{k} ({v})" for k, v in list(summary["unknown"].items())[:5])
        raise ToolError(f"unclassified {TOOL} statuses (not counted as killed): {shown}")

    diffs = {k: _diff(python, copy, env, k, deadline) for k in summary["survivors"]}
    warnings = []
    complete = not any(results[k] == "not checked" for k in results)
    if not complete:
        warnings.append("some mutants were never checked; result is partial")
    if summary["killed"] == 0:
        warnings.append("no mutant was killed: the suite may be genuinely weak, or the run is not measuring it")
    elapsed = time.monotonic() - started
    out_path = write_result(config.repo, "mutate", module, _result(
        summary, diffs, len(results), complete, elapsed, warnings, tests, version))
    return out_path, (0 if complete else 2)


def _partial(config, module, tests, python, copy, env, started, budget_seconds):
    """Budget exhausted: the process group is already dead. Record what we have."""
    version = _mutmut_version()
    warnings = [f"budget of {budget_seconds}s exhausted; result is partial"]
    results: dict[str, str] = {}
    try:
        results = _collect(python, copy, env, time.monotonic() + 30)
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
    write_result(config.repo, "mutate", module, _result(
        summary, diffs, len(results), False, time.monotonic() - started, warnings, tests, version))
    print(f"testloop: mutate budget of {budget_seconds}s exhausted; partial result written (complete: false)",
          file=sys.stderr)
    from common import result_path

    # Returns (path, 2): the CLI prints the path and exits 2, and the partial result is
    # marked complete: false.
    return result_path(config.repo, "mutate", module), 2
