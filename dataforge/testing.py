"""pytest plugin shared by the engine's tests and a workspace's tests (df-xkhx.7).

Both suites load it with `pytest_plugins = ["dataforge.testing"]` in their conftest.py. They cannot
share one conftest: each has its own `tests` package, and two packages of that name do not fit in one
pytest process (CI runs them as separate jobs). What both need lives here once: the Hypothesis
profiles, the rule that a leaked connection or file is an error, and the fixtures that run a job
through the real engine on a temp DATAFORGE_HOME. Test code only: nothing in the package imports it,
and prod (which has no pytest) never loads it.
"""
import gc

import pytest
from hypothesis import settings

from dataforge.compat import db_url as make_db_url
from dataforge.db import init_db

# `testloop check` and `mutate` pass --hypothesis-profile=deterministic (testloop.toml): every run of a
# property tries the same examples, so a verdict does not depend on the draw.
settings.register_profile("deterministic", derandomize=True, database=None, deadline=None)
# Ordinary runs: the profile Hypothesis picked on its own ("default" locally: random, with the example
# database; "ci" when it detects CI, which already has no deadline), without the 200 ms per-example
# deadline, so a property on a slow disk fails on its rule, never on time. Hypothesis's pytest plugin
# loads a profile named on the command line (--hypothesis-profile) after this module is imported, so
# that one still wins.
settings.register_profile("no-deadline", settings.default, deadline=None)
# CI's orders job (df-91aq): new examples on every run. Hypothesis's own "ci" profile, which every other
# CI run starts from, is derandomized: each property tries the same examples on every PR. Here they
# are drawn from --hypothesis-seed, which the job prints, so a failure reproduces locally with the
# same seed. No example database (a CI runner keeps nothing), and the failing example's blob printed.
settings.register_profile("explore", derandomize=False, database=None, deadline=None, print_blob=True)
settings.load_profile("no-deadline")


def pytest_configure(config):
    """A connection or file left open fails the test that left it, in every suite that loads this
    plugin (df-n8ac.19, df-g4z7).

    Python warns of it from the object's finalizer, where nothing can be raised: pytest turns that
    into its own PytestUnraisableExceptionWarning, so both are errors. Hundreds of `unclosed
    database` lines per run went unread for weeks while every caller of init_db leaked a pooled
    connection."""
    config.addinivalue_line("filterwarnings", "error::ResourceWarning")
    config.addinivalue_line("filterwarnings", "error::pytest.PytestUnraisableExceptionWarning")


@pytest.fixture(scope="module", autouse=True)
def leaks_surface_in_their_module():
    """Collect garbage when a test module ends, so what its tests left open fails there.

    An object in a reference cycle is closed only by a garbage collection, which may come many
    tests later or never: test_report_scripts leaked a connection per test and whole runs passed.
    Collected here, the leak fails the run every time, in the teardown of the last test this
    process ran from the module that opened it. Once per module: a collection after every test
    would cost more than the tests."""
    yield
    gc.collect()


_LEAK_MARKS = ("ResourceWarning", "PytestUnraisableExceptionWarning")
LEAK_HINT = (
    "A connection or file was left open. The test that failed is the one that was running when Python "
    "noticed, which is often not the one that opened it: an object in a reference cycle is only "
    "found by a garbage collection, at the latest in the teardown of the last test of its module. "
    "To see where each one was opened, run the module (or the suite) with\n"
    "  PYTHONTRACEMALLOC=20 pytest -p no:randomly -W always::ResourceWarning "
    "-W always::pytest.PytestUnraisableExceptionWarning\n"
    "and read the 'Object allocated at' block of each warning."
)


def pytest_terminal_summary(terminalreporter):
    """Say what a leak failure means: read alone, it looks like a flaky test (df-g4z7)."""
    failed = terminalreporter.stats.get("failed", []) + terminalreporter.stats.get("error", [])
    if any(mark in str(report.longrepr) for report in failed for mark in _LEAK_MARKS):
        terminalreporter.section("leaked connection or file")
        terminalreporter.write_line(LEAK_HINT)


@pytest.fixture
def db_url(tmp_path):
    """Isolated SQLite DB per test. Never use a real path."""
    return make_db_url(tmp_path / "dataforge.db")


@pytest.fixture
def dataforge_home(tmp_path, monkeypatch):
    """Sets DATAFORGE_HOME to a temp dir and creates workspaces/ subdirectory."""
    home = tmp_path / "dataforge_home"
    (home / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("DATAFORGE_HOME", str(home))
    return home


@pytest.fixture
def global_engine(dataforge_home):
    """SQLAlchemy engine for the global DB at DATAFORGE_HOME/dataforge.db."""
    return init_db(make_db_url(dataforge_home / "dataforge.db"))
