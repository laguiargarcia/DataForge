"""`map`: uncovered branches of a module and which tests execute each line."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from common import Config, ToolError, resolve_tests, write_result


def _dotted(module: str) -> str:
    return module.removesuffix(".py").replace("/", ".")


def _run(cmd: list[str], config: Config, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=config.repo, env=env, capture_output=True, text=True)


def _clean_contexts(contexts: list[str]) -> list[str]:
    cleaned = {c.removesuffix("|run") for c in contexts if c}
    return sorted(c for c in cleaned if c)


def _line_text(source_lines: list[str], line: int) -> str:
    return source_lines[line - 1].strip() if 0 < line <= len(source_lines) else ""


def run(config: Config, module: str) -> dict:
    source = config.repo / module
    if not source.is_file():
        raise ToolError(f"module not found: {module}")
    tests = resolve_tests(config, module)

    with tempfile.TemporaryDirectory(prefix="testloop-") as tmp:
        env = {**os.environ, **config.env, "COVERAGE_FILE": str(Path(tmp) / ".coverage")}
        report = Path(tmp) / "coverage.json"
        pytest_cmd = [
            config.python, "-m", "pytest", *config.pytest_args, *config.check_args, *tests,
            f"--cov={_dotted(module)}", "--cov-branch", "--cov-context=test",
            "--cov-report=",
        ]
        proc = _run(pytest_cmd, config, env)
        if proc.returncode not in (0, 1):
            raise ToolError(f"pytest exited {proc.returncode}:\n{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
        cov = _run(
            [config.python, "-m", "coverage", "json", "--show-contexts", "-o", str(report)],
            config, env,
        )
        if cov.returncode != 0 or not report.is_file():
            raise ToolError(f"no coverage data for {module}: {cov.stdout}{cov.stderr}")
        files = json.loads(report.read_text(encoding="utf-8"))["files"]

    entry = next((v for k, v in files.items() if Path(k).as_posix() == module), None)
    if entry is None or not entry["executed_lines"]:
        raise ToolError(f"{module} has no coverage data (never imported by its tests?)")

    source_lines = source.read_text(encoding="utf-8").splitlines()
    summary = entry["summary"]
    return {
        "tests": tests,
        "summary": {
            "num_branches": summary.get("num_branches", 0),
            "covered_branches": summary.get("covered_branches", 0),
            "missing_lines": summary.get("missing_lines", 0),
        },
        "uncovered_branches": [
            {"from": a, "to": b, "line_text": _line_text(source_lines, a)}
            for a, b in entry.get("missing_branches", [])
        ],
        "line_tests": {
            line: names
            for line, ctxs in sorted(entry.get("contexts", {}).items(), key=lambda kv: int(kv[0]))
            if (names := _clean_contexts(ctxs))
        },
    }


def main(config: Config, module: str, **options) -> Path:
    return write_result(config.repo, "map", module, run(config, module))
