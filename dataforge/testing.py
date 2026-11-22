"""pytest plugin shared by the engine's tests and a workspace's tests (df-xkhx.7).

Both suites load it with `pytest_plugins = ["dataforge.testing"]` in their conftest.py. They cannot
share one conftest: each has its own `tests` package, and two packages of that name do not fit in one
pytest process (CI runs them as separate jobs). What both need lives here once: the Hypothesis
profiles and the fixtures that run a job through the real engine on a temp DATAFORGE_HOME. Test code
only: nothing in the package imports it, and prod (which has no pytest) never loads it.
"""
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
