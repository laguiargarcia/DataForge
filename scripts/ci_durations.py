"""Merge the test durations each CI shard stored into the one file the next run splits by (df-n8ac.13).

Each shard runs pytest-split with --store-durations --clean-durations, so its file holds exactly the
tests it ran; the shards' groups are disjoint, so the merge is the whole suite as it is today (a
deleted test drops out). The `tests` and `tests-windows` jobs download the shards' artifacts into
durations/<artifact>/.test_durations, run this from the repository root, and cache .test_durations,
which the next run's shards restore.

    python scripts/ci_durations.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SHARDS = "durations/*/.test_durations"
OUT = Path(".test_durations")


def merge(parts: list[dict[str, float]]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for part in parts:
        merged.update(part)
    return dict(sorted(merged.items()))


def main() -> int:
    files = sorted(Path().glob(SHARDS))
    if not files:
        print(f"ci_durations: no {SHARDS}; nothing to merge", file=sys.stderr)
        return 1
    merged = merge([json.loads(f.read_text(encoding="utf-8")) for f in files])
    OUT.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")
    print(f"ci_durations: {len(merged)} tests from {len(files)} shard file(s) -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
