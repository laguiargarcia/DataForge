"""Shared pieces of the test loop CLI: config, result files, errors."""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

TOOL_NAME = "testloop"
TOOL_VERSION = "0.1.0"
RESULT_DIR = ".testloop"


class ToolError(Exception):
    """Usage or tool error: no result can be trusted (CLI exit code 2)."""


@dataclass
class Config:
    repo: Path
    python: str = "python"
    pytest_args: list[str] = field(default_factory=list)
    check_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    targets: dict[str, dict] = field(default_factory=dict)


def load_config(repo: Path) -> Config:
    repo = Path(repo)
    path = repo / "testloop.toml"
    if not path.is_file():
        raise ToolError(f"testloop.toml not found in {repo}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ToolError(f"invalid testloop.toml: {exc}") from exc
    return Config(
        repo=repo,
        python=raw.get("python", "python"),
        pytest_args=list(raw.get("pytest_args", [])),
        check_args=list(raw.get("check_args", [])),
        env={k: str(v) for k, v in raw.get("env", {}).items()},
        targets=dict(raw.get("targets", {})),
    )


def resolve_tests(config: Config, module: str) -> list[str]:
    """Test files (repo-relative, posix, sorted) for a module; ToolError if none."""
    target = config.targets.get(module)
    if not target or not target.get("tests"):
        raise ToolError(
            f"{module} has no [targets.\"{module}\"] entry with `tests` in testloop.toml; add one"
        )
    patterns = target["tests"]
    found: set[str] = set()
    for pattern in patterns:
        for p in config.repo.glob(pattern):
            if p.is_file():
                found.add(p.relative_to(config.repo).as_posix())
    if not found:
        raise ToolError(f"no tests match {patterns} for {module}")
    return sorted(found)


def source_sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def result_path(repo: Path, command: str, module: str) -> Path:
    return Path(repo) / RESULT_DIR / f"{command}-{module.replace('/', '_')}.json"


def clear_result(repo: Path, command: str, module: str) -> None:
    """Delete a previous result so a failed run never leaves a stale one behind."""
    result_path(repo, command, module).unlink(missing_ok=True)


def write_result(repo: Path, command: str, module: str, data: dict) -> Path:
    """Write the JSON with the common fields (command, module, tests, source_sha,
    generated_at, tool) merged in; return its path."""
    payload = {
        "command": command,
        "module": module,
        "tests": [],
        "source_sha": source_sha(Path(repo) / module),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }
    payload.update(data)
    out = result_path(repo, command, module)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
