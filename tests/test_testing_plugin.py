"""dataforge/testing.py, the pytest plugin both suites load: its Hypothesis profiles (df-91aq).

On CI, Hypothesis loads its own "ci" profile, which is derandomized: a property tries the same examples
on every run. The orders job passes --hypothesis-profile=explore --hypothesis-seed=<seed> so the
examples change with the seed and come back with it. Each case runs a property in a pytest child with
CI=true, as on a runner, and compares the examples it saw.
"""
import os
import subprocess
import sys

import pytest

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
