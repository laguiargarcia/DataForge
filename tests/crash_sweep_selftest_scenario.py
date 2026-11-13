"""Scenario for tests/test_crash_sweep.py, the helper's own test: three plain writes, no
dataforge import, so each child starts fast."""
import os
from pathlib import Path


def three_writes(workdir: Path) -> None:
    (workdir / "a").write_bytes(b"aaaa")
    os.replace(workdir / "a", workdir / "b")
    (workdir / "c").write_bytes(b"cccc")
