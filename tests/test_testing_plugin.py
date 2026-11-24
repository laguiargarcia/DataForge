"""dataforge/testing.py, the pytest plugin both suites load: its Hypothesis profiles (df-91aq) and its
rule that a connection or file left open fails the run (df-n8ac.19, df-g4z7).

On CI, Hypothesis loads its own "ci" profile, which is derandomized: a property tries the same examples
on every run. The orders job passes --hypothesis-profile=explore --hypothesis-seed=<seed> so the
examples change with the seed and come back with it. Each case runs a property in a pytest child with
CI=true, as on a runner, and compares the examples it saw.
"""
import os
import subprocess
import sys

import pytest

from dataforge.testing import LEAK_HINT

_TEST = """import os
from hypothesis import given, strategies as st


@given(st.integers())
def test_seen(x):
    with open(os.environ["SEEN"], "a", encoding="utf-8") as f:
        f.write(f"{x}\\n")
"""


@pytest.fixture
def seen(tmp_path):
    """Run the property once with `args`; return the examples it tried, in order."""
    (tmp_path / "conftest.py").write_text('pytest_plugins = ["dataforge.testing"]\n', encoding="utf-8")
    (tmp_path / "test_seen.py").write_text(_TEST, encoding="utf-8")
    runs = iter(range(100))

    def run(*args: str) -> list[str]:
        out = tmp_path / f"seen-{next(runs)}.txt"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", *args,
             "test_seen.py"],
            cwd=tmp_path, env={**os.environ, "CI": "true", "SEEN": str(out)},
            capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return out.read_text(encoding="utf-8").splitlines()
    return run


def test_on_ci_the_default_profile_tries_the_same_examples_every_run(seen):
    first_run = seen()
    assert seen() == first_run


def test_the_explore_profile_draws_from_the_seed_and_repeats_with_it(seen):
    first = seen("--hypothesis-profile=explore", "--hypothesis-seed=1")
    assert seen("--hypothesis-profile=explore", "--hypothesis-seed=1") == first
    assert seen("--hypothesis-profile=explore", "--hypothesis-seed=2") != first
    assert first != seen()


_LEAK = """def test_leaves_a_file_open():
    open(__file__, encoding="utf-8")
"""
_CLEAN = """def test_closes_its_file():
    with open(__file__, encoding="utf-8") as f:
        assert f.read()


def test_fails_on_its_own():
    assert False
"""


def _run_suite(tmp_path, body: str) -> subprocess.CompletedProcess:
    (tmp_path / "conftest.py").write_text('pytest_plugins = ["dataforge.testing"]\n', encoding="utf-8")
    (tmp_path / "test_it.py").write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", "test_it.py"],
        cwd=tmp_path, capture_output=True, text=True, check=False)


def test_a_file_left_open_fails_the_run_and_the_summary_says_how_to_find_who_opened_it(tmp_path):
    proc = _run_suite(tmp_path, _LEAK)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "1 failed" in proc.stdout
    assert LEAK_HINT in proc.stdout


def test_a_failure_of_another_kind_gets_no_leak_summary(tmp_path):
    proc = _run_suite(tmp_path, _CLEAN)
    assert "1 failed, 1 passed" in proc.stdout, proc.stdout + proc.stderr
    assert "leaked connection or file" not in proc.stdout


_LEAK_IN_A_CYCLE = """def test_leaves_a_file_open_in_a_cycle():
    holder = [open(__file__, encoding="utf-8")]
    holder.append(holder)


def test_runs_after_it():
    pass
"""


def test_a_file_left_open_in_a_reference_cycle_fails_its_own_module(tmp_path):
    """Nothing closes it until a garbage collection, which the plugin runs when the module ends."""
    proc = _run_suite(tmp_path, _LEAK_IN_A_CYCLE)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "2 passed, 1 error" in proc.stdout
    assert LEAK_HINT in proc.stdout
