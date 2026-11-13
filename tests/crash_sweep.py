"""Crash sweep: kill a scenario at every write and let the caller check what recovery finds.

A process can die at any moment (power loss, kill, out of memory). Then no `finally`, `except` or
atexit handler runs, so the kill here is a real process exit: `os._exit` in a child process.

    points = WritePoints(["os.replace", "pathlib.Path.write_bytes"],
                         torn=("pathlib.Path.write_bytes",))
    steps = crash_sweep("tests.x_scenarios:run", make_workdir, points, check_recovered)

- scenario: "module:function"; the child imports it and calls `function(workdir)`.
- make_workdir(name): the parent builds a fresh starting state and returns its directory. The dry
  run gets make_workdir("dry") and finishes normally, so the caller can check the end state there.
- points.names: dotted names of the calls that make state durable for this target, such as
  "os.replace" or "pathlib.Path.write_bytes". Each call to one of them is one write point. A name
  like "pkg.module.open" that is a builtin there is patched in that module only.
- points.torn: write points that can also die half-way: the child makes the call with every
  bytes/str argument cut in half, then exits. Only for calls whose single bytes/str argument is
  the data.
- points.after: write points where the call completes and the child dies right after it, before
  anything else runs. This reaches writes that cannot be patched themselves, such as a
  file object's write after `open(path, "w")` has already truncated the file.
- check_recovered(workdir, step): runs in the parent after each crash; `step` is a Step.

A dry run lists the write points (N of them). Then the child is killed before each one, inside
each torn one and after each `after` one, WORKERS children at a time. A child that does not die
with CRASH_CODE at the same write point the dry run saw there fails the sweep.
"""
import builtins
import importlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, NamedTuple

CRASH_CODE = 113            # the child's exit code when it dies at the chosen write point
TIMEOUT = 120               # seconds one child may take
WORKERS = min(8, os.cpu_count() or 1)   # children running at the same time
_ROOT = Path(__file__).resolve().parent.parent
_MARK = "crash-sweep-died-at "   # the child writes this line, then the point, just before dying
_SEEN = "crash-sweep-reached "   # the dry run writes one such line per write point it reached


class WritePoints(NamedTuple):
    names: list[str]        # every call that makes state durable (one write point per call)
    torn: tuple[str, ...] = ()    # also die half-way through these
    after: tuple[str, ...] = ()   # also die right after these complete


class Step(NamedTuple):
    k: int                  # 1-based index of the write point the child died at
    point: str              # its dotted name
    how: str                # "before" the call, "torn" half-way through it, or "after" it

    def __str__(self) -> str:
        return f"k={self.k} {self.how} {self.point}"


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


def _die(k: int, dotted: str) -> None:
    os.write(1, f"\n{_MARK}{k} {dotted}\n".encode())   # os._exit flushes nothing, so no print
    os._exit(CRASH_CODE)


def _install(points: list[str], step: Step, seen: list[str]) -> None:
    def patch(dotted: str) -> None:
        owner, name = _resolve(dotted)
        original = getattr(owner, name, None) or getattr(builtins, name)

        def counted(*args, **kwargs):
            seen.append(dotted)
            if len(seen) != step.k:
                return original(*args, **kwargs)
            if step.how == "torn":
                original(*[_half(a) for a in args], **kwargs)
            elif step.how == "after":
                original(*args, **kwargs)
            return _die(step.k, dotted)

        setattr(owner, name, counted)

    for dotted in points:
        patch(dotted)


def _child(scenario: str, workdir: str, k: str, how: str, *points: str) -> None:
    module, func = scenario.split(":")
    run = getattr(importlib.import_module(module), func)   # imported before the patches go in
    seen: list[str] = []
    _install(list(points), Step(int(k), "", how), seen)
    run(Path(workdir))
    print("".join(f"\n{_SEEN}{point}" for point in seen))  # reached only when nothing killed it


def _run(scenario: str, workdir: Path, step: Step, points: list[str]):
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    cmd = [sys.executable, "-m", "tests.crash_sweep", scenario, str(workdir), str(step.k),
           step.how, *points]
    return subprocess.run(cmd, cwd=_ROOT, env=env, capture_output=True, text=True,
                          timeout=TIMEOUT, check=False)


def _marked(stdout: str, mark: str) -> list[str]:
    """The lines the child wrote after `mark`; the scenario may print anything else around them."""
    return [line[len(mark):] for line in stdout.splitlines() if line.startswith(mark)]


def _died_at(stdout: str) -> str:
    return (_marked(stdout, _MARK) or [""])[-1]


def crash_sweep(scenario: str, make_workdir: Callable[[str], Path], points: WritePoints,
                check_recovered: Callable[[Path, Step], None]) -> list[Step]:
    """Run the sweep described in the module docstring; return the steps that were crashed."""
    write_points, torn, after = points
    unknown = (set(torn) | set(after)) - set(write_points)
    assert not unknown, f"torn/after points that are not write points: {sorted(unknown)}"
    dry = _run(scenario, make_workdir("dry"), Step(0, "", "before"), write_points)
    assert dry.returncode == 0, f"dry run failed ({dry.returncode}):\n{dry.stdout}{dry.stderr}"
    seen = _marked(dry.stdout, _SEEN)
    assert seen, f"the scenario reached none of the write points {write_points}"
    steps = [Step(k, point, how) for k, point in enumerate(seen, start=1)
             for how, extra in (("before", True), ("torn", point in torn), ("after", point in after))
             if extra]
    workdirs = [make_workdir(f"k{step.k}-{step.how}") for step in steps]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:   # the children only; checks run in order
        deaths = list(pool.map(lambda step, workdir: _run(scenario, workdir, step, write_points),
                               steps, workdirs))
    for step, workdir, died in zip(steps, workdirs, deaths, strict=True):
        assert (died.returncode, _died_at(died.stdout)) == (CRASH_CODE, f"{step.k} {step.point}"), (
            f"{step}: the child did not die at this write point (exit {died.returncode}, "
            f"died at {_died_at(died.stdout)!r}):\n{died.stdout}{died.stderr}")
        check_recovered(workdir, step)
    return steps


if __name__ == "__main__":
    _child(*sys.argv[1:])
