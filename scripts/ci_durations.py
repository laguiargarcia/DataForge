"""Merge the test durations each CI shard stored into the one file the next run splits by (df-n8ac.13).

Each shard runs pytest-split with --store-durations --clean-durations, so its file holds exactly the
tests it ran; the shards' groups are disjoint, so the merge is the whole suite as it is today (a
deleted test drops out). The `tests` and `tests-windows` jobs run this and cache the result, which
the next run's shards restore.

    python scripts/ci_durations.py OUT SHARD_FILE...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def merge(parts: list[dict[str, float]]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for part in parts:
        merged.update(part)
    return dict(sorted(merged.items()))


def main(argv: list[str]) -> int:
    out, *files = (Path(a) for a in argv) if argv else (None,)
    if not files:
        print(__doc__, file=sys.stderr)
        return 2
    merged = merge([json.loads(f.read_text(encoding="utf-8")) for f in files])
    out.write_text(json.dumps(merged, indent=1) + "\n", encoding="utf-8")
    print(f"ci_durations: {len(merged)} tests from {len(files)} shard file(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
