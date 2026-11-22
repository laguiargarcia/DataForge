"""Scenarios the GetFinance crash sweep runs in a child process (tests/test_crash_sweep_getfinance.py,
df-xkhx.2). Kept out of the test module so the child does not import pytest. The child imports this
module as workspaces.GetFinance.tests.getfinance_crash_scenarios (the repo root is its cwd, where
`tests` is the engine's package), so the imports below are relative or by path."""
import sqlite3
import sys
from pathlib import Path

from . import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))   # edicao imports classificacao by bare name

import edicao  # needs the path above

EDICAO_DB = "edicao.db"


class Conn(sqlite3.Connection):
    """A connection whose `execute` the sweep can patch: sqlite3.Connection is a C type, whose
    methods cannot be replaced, so each statement of a write is one write point here."""


def raw2cleansed(workdir: Path) -> None:
    """One run of raw2cleansed.py on <workdir>'s raw/ and cleansed/: the transactions table is a Delta
    MERGE that accumulates (a DELETE of the ids gone from the latest fetch, then an upsert)."""
    _delta_util.run_script("raw2cleansed", "raw2cleansed", ["--source", "raw", "--target", "cleansed"], workdir)


def renomear_categoria(workdir: Path) -> None:
    """The app renames a subcategory: the dictionary, the rules and the corrections that point to it,
    and the version, in one write (edicao.salvar_categoria)."""
    conn = sqlite3.connect(workdir / EDICAO_DB, factory=Conn)
    edicao.salvar_categoria(conn, "Mercado", "Supermercado", "Alimentação", "Essencial")
    conn.close()
