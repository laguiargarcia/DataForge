"""Measured mutation costs (df-n8ac.39): the costs file `mutate --costs` and `affected.py --matrix
--costs` read, built from the mutate results in .testloop/.

Usage (repo root): costs.py --out FILE

For each testloop.toml target it reads the results of its current `shards` (as gate does) and writes
{module: {"elapsed": seconds of its shards summed, "functions": {function key: seconds}}}, the
functions merged from each result's `function_seconds`. Only complete results measure: one the
budget cut short timed the budget, not the shard. A target with a shard missing or partial keeps the
functions of the others but no elapsed (it would be wrong), and one without any complete result is
left out: affected estimates both as unmeasured. CI builds it in mutation-ok from a full run (push, manual run).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ToolError, load_config, result_path, shard_count


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _seconds(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def measure(config, module: str) -> dict | None:
    """The module's entry in the costs file, or None when no shard of it left a complete result."""
    n = shard_count(config, module)
    results = [_read(result_path(config.repo, "mutate", module, (i, n))) for i in range(1, n + 1)]
    found = [data for data in results if data is not None and data.get("complete") is True]
    if not found:
        return None
    functions: dict[str, float] = {}
    for data in found:
        for key, seconds in (data.get("function_seconds") or {}).items():
            if _seconds(seconds) is not None:
                functions[key] = round(functions.get(key, 0.0) + seconds, 3)
    elapsed = [_seconds(data.get("elapsed_s")) for data in found]
    entry: dict = {"functions": dict(sorted(functions.items()))}
    if len(found) == n and None not in elapsed:
        entry = {"elapsed": round(sum(elapsed), 2), **entry}
    return entry


def build(config) -> dict[str, dict]:
    """The costs file of every target that left a result."""
    return {module: entry for module in sorted(config.targets) if (entry := measure(config, module)) is not None}


def main(argv: list[str] | None = None) -> int:
    # The costs file goes to stdout (the CI redirects it): no output path to trust (Sonar S8707).
    argparse.ArgumentParser(prog="costs", description="print the costs file of the last results").parse_args(argv)
    try:
        config = load_config(Path.cwd())
        costs = build(config)
    except ToolError as exc:
        print(f"costs: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(costs, indent=2, sort_keys=True))
    timed = sum("elapsed" in entry for entry in costs.values())
    print(f"costs: {timed} of {len(config.targets)} targets measured", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
