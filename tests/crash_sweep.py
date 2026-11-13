"""Crash sweep: kill a scenario at every write and let the caller check what recovery finds.

A process can die at any moment (power loss, kill, out of memory). Then no `finally`, `except` or
atexit handler runs, so the kill here is a real process exit: `os._exit` in a child process.

    steps = crash_sweep("tests.test_x:scenario", make_workdir, WRITE_POINTS, check_recovered)

- scenario: "module:function"; the child imports it and calls `function(workdir)`.
- make_workdir(name): the parent builds a fresh starting state and returns its directory.
- write_points: dotted names of the calls that make state durable for this target, such as
  "os.replace" or "pathlib.Path.write_bytes". Each call to one of them is one write point.
- torn: the write points that can also die half-way; the child calls them with every bytes/str
  argument cut in half and then exits.
- check_recovered(workdir, step): runs in the parent after each crash; `step` is a Step.

A dry run lists the write points (N of them). Then the child is killed before each one, and once
more inside each torn one, WORKERS children at a time. A child that does not die with
CRASH_CODE fails the sweep.
"""
import importlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, NamedTuple

CRASH_CODE = 113            # the child's exit code when it dies at the chosen write point
TIMEOUT = 120               # seconds one child may take
WORKERS = 4                 # children running at the same time
_ROOT = Path(__file__).resolve().parent.parent


class Step(NamedTuple):
    k: int                  # 1-based index of the write point the child died at
    point: str              # its dotted name
    torn: bool              # False: died before the write; True: died after half of it


def _resolve(dotted: str):
    """Return (owner, attribute name) for a dotted name: the longest importable prefix is the
    module, the rest are attributes."""
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        try:
            owner = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for name in parts[cut:-1]:
            owner = getattr(owner, name)
        return owner, parts[-1]
    raise ImportError(f"no importable module in {dotted!r}")


def _half(arg):
    return arg[:len(arg) // 2] if isinstance(arg, (bytes, str)) else arg


def _install(points: list[str], k: int, torn: bool, seen: list[str]) -> None:
    def patch(dotted: str) -> None:
        owner, name = _resolve(dotted)
        original = getattr(owner, name)

        def counted(*args, **kwargs):
            seen.append(dotted)
            if len(seen) == k:
                if torn:
                    original(*[_half(a) for a in args], **kwargs)
                os._exit(CRASH_CODE)
            return original(*args, **kwargs)

        setattr(owner, name, counted)

    for dotted in points:
        patch(dotted)


def _child(scenario: str, workdir: str, k: str, torn: str, *points: str) -> None:
    module, func = scenario.split(":")
    run = getattr(importlib.import_module(module), func)   # imported before the patches go in
    seen: list[str] = []
    _install(list(points), int(k), torn == "1", seen)
    run(Path(workdir))
    print("\n".join(seen))                                 # reached only when nothing killed it


def _run(scenario: str, workdir: Path, step: Step, points: list[str]):
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [sys.executable, "-m", "tests.crash_sweep", scenario, str(workdir), str(step.k),
           "1" if step.torn else "0", *points]
    return subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT, check=False)


def crash_sweep(scenario: str, make_workdir: Callable[[str], Path], write_points: list[str],
                check_recovered: Callable[[Path, Step], None],
                torn: tuple[str, ...] = ()) -> list[Step]:
    """Run the sweep described in the module docstring; return the steps that were crashed."""
    dry = _run(scenario, make_workdir("dry"), Step(0, "", False), write_points)
    assert dry.returncode == 0, f"dry run failed ({dry.returncode}):\n{dry.stdout}{dry.stderr}"
    seen = dry.stdout.split()
    assert seen, f"the scenario reached none of the write points {write_points}"
    steps = [Step(k, point, tear) for k, point in enumerate(seen, start=1)
             for tear in (False, True) if not tear or point in torn]
    workdirs = [make_workdir(f"k{step.k}{'-torn' if step.torn else ''}") for step in steps]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:   # the children only; checks run in order
        deaths = list(pool.map(lambda step, workdir: _run(scenario, workdir, step, write_points),
                               steps, workdirs))
    for step, workdir, died in zip(steps, workdirs, deaths, strict=True):
        assert died.returncode == CRASH_CODE, (
            f"{step}: the child did not die at this write point (exit {died.returncode}):\n"
            f"{died.stdout}{died.stderr}")
        check_recovered(workdir, step)
    return steps


if __name__ == "__main__":
    _child(*sys.argv[1:])
