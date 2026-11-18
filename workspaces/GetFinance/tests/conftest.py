"""Fixtures shared by the pipeline tests.

The engine fixtures mirror DataForge's tests/conftest.py, for the tests that run this workspace's
job.yaml through the real engine (test_job_wiring). Every database and DATAFORGE_HOME is a temp dir.
"""
import pytest

from dataforge.compat import db_url as make_db_url
from dataforge.db import init_db


@pytest.fixture
def dataforge_home(tmp_path, monkeypatch):
    """DATAFORGE_HOME in a temp dir, with an empty workspaces/."""
    home = tmp_path / "dataforge_home"
    (home / "workspaces").mkdir(parents=True)
    monkeypatch.setenv("DATAFORGE_HOME", str(home))
    return home


@pytest.fixture
def db_url(tmp_path):
    """An isolated SQLite database per test."""
    return make_db_url(tmp_path / "dataforge.db")


@pytest.fixture
def global_engine(dataforge_home):
    """SQLAlchemy engine for the global database at DATAFORGE_HOME/dataforge.db."""
    return init_db(make_db_url(dataforge_home / "dataforge.db"))
