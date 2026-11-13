"""`check`: the deterministic judge. Runs the module's tests and reports pass/fail."""

import subprocess
from collections.abc import Sequence
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from common import Config, ToolError, pytest_args, resolve_tests, test_env, write_result


def _nodeid(repo: Path, classname: str, name: str, tests: Sequence[str] = ()) -> str:
    """Rebuild a pytest node id from a junit classname (dotted path[.Class]) and name.

    The classname is dotted from pytest's rootdir, which can sit above the repo (a worktree picks
    up the clone's pytest.ini), so a dot in a folder name ('.worktrees/df-ahua.23a') is ambiguous.
    The module's own test files are matched first, at a dot boundary; then any file on disk."""
    padded = f".{classname}."
    for test in sorted(tests, key=len, reverse=True):
        key = "." + test.removesuffix(".py").replace("/", ".") + "."
        at = padded.find(key)
        if at >= 0:
            rest = padded[at + len(key):-1]  # the class path after the file, "" when none
            return "::".join([test, *(rest.split(".") if rest else []), name])
    parts = classname.split(".") if classname else []
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if (repo / rel).is_file():
            return "::".join([rel, *parts[i:], name])
    return "::".join([*parts, name]) if parts else name


def _parse(repo: Path, junit: Path, tests: Sequence[str] = ()) -> tuple[int, list[str], list[str], list[str]]:
    if not junit.is_file():
        return 0, [], [], []
    collected, failed, xfailed, skipped_ids = 0, [], [], []
    for case in ET.parse(junit).getroot().iter("testcase"):
        collected += 1
        nodeid = _nodeid(repo, case.get("classname", ""), case.get("name", ""), tests)
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(nodeid)
        else:
            skipped = case.find("skipped")
            if skipped is not None and skipped.get("type") == "pytest.xfail":
                xfailed.append(nodeid)
            elif skipped is not None:
                skipped_ids.append(nodeid)
    return collected, sorted(failed), sorted(xfailed), sorted(skipped_ids)


def main(config: Config, module: str) -> tuple[Path, int]:
    if not (config.repo / module).is_file():
        raise ToolError(f"module not found: {module}")
    tests = resolve_tests(config, module)

    with tempfile.TemporaryDirectory(prefix="testloop-") as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [
            config.python, "-m", "pytest", *pytest_args(config), f"--junitxml={junit}", *tests,
        ]
        env = test_env(config)
        try:
            proc = subprocess.run(cmd, cwd=config.repo, env=env, capture_output=True, text=True)
        except OSError as exc:
            raise ToolError(f"cannot run {config.python}: {exc}") from exc
        collected, failed, xfailed, skipped = _parse(config.repo, junit, tests)

    # pytest exit 0 = all passed, 1 = some failed; anything else (usage error 4,
    # internal error 3, interrupted 2, nothing collected 5) is a tool error.
    ran = collected - len(skipped)  # passed, failed or xfailed
    if proc.returncode == 0 and ran > 0:
        code, passed, error = 0, True, None
    elif proc.returncode == 1 and failed:
        code, passed, error = 1, False, None
    else:
        code, passed = 2, False
        error = f"pytest exited {proc.returncode}; {collected} collected, {ran} actually ran"
        print(f"testloop: {error}\n{(proc.stdout + proc.stderr)[-1500:]}", file=sys.stderr)

    out = write_result(config.repo, "check", module, {
        "tests": tests,
        "passed": passed,
        "exit_code": proc.returncode,
        "collected": collected,
        "failed": failed,
        "xfailed": xfailed,
        "skipped": skipped,
        "error": error,
    })
    return out, code
