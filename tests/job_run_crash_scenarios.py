"""Scenario the job-run crash sweep runs in a child process (tests/test_crash_sweep_job_run.py).
Kept out of the test module so the child does not import pytest."""
import os
from pathlib import Path
from dataforge.cli import run


def run_job(workdir: Path) -> None:
    """`dataforge run w j` (what the OS scheduler calls) with <workdir> as DATAFORGE_HOME."""
    os.environ["DATAFORGE_HOME"] = str(workdir)
    run("w", "j")
