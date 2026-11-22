"""Crash sweep of GetFinance's accumulating writes (df-xkhx.2): the state they leave cannot be rebuilt
from scratch, so a process that dies in the middle must not leave it wrong.

- cleansed.transactions: a Delta table that accumulates (ids gone from Pluggy's window persist only
  there). raw2cleansed.py is killed before and after each Delta commit; then the next run of the same
  script, on the same raw, must end with the table the uncrashed run wrote. That is the promise of a
  daily job that may die any day: the next day's run puts the table right (raw2cleansed.py, "Accumulate").
- edicao.db: the owner's edits. edicao.py promises that each write validates, writes and bumps the
  version in one transaction. A kill at each statement of a rename must leave either the whole edit
  with the version bumped, or nothing.

The helper is DataForge's tests/crash_sweep.py, loaded by path: the engine's `tests` package and this
workspace's cannot share one process (docs/testing.md, "Crash sweep").
"""
import importlib.util
from pathlib import Path

import polars as pl

from tests import _delta_util, getfinance_crash_scenarios as scenarios
from tests.test_raw2cleansed import _seed_raw, _txn, BANK_AND_CARD, STATUS

_HELPER = _delta_util.ROOT.parents[1] / "tests" / "crash_sweep.py"
_spec = importlib.util.spec_from_file_location("dataforge_crash_sweep", _HELPER)
crash_sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crash_sweep)

_SCENARIOS = "workspaces.GetFinance.tests.getfinance_crash_scenarios"


# ---------------------------------------------------------------- cleansed.transactions (Delta MERGE)

# Every Delta commit raw2cleansed makes: an overwrite goes through write_delta, and a MERGE commits at
# TableMerger.execute (write_delta in mode "merge" only builds it).
_DELTA = crash_sweep.WritePoints(["polars.DataFrame.write_delta", "deltalake.table.TableMerger.execute"],
                                 after=("polars.DataFrame.write_delta", "deltalake.table.TableMerger.execute"))


def _fetch(workdir: Path, rows: list[dict], day: str) -> None:
    _seed_raw(workdir / "raw", rows, BANK_AND_CARD, day, extra_cols=STATUS)


def _cleansed_before_the_second_fetch(root: Path, name: str) -> Path:
    """A cleansed.transactions with A, P (PENDING), K and L (card), then a fetch where P and K are gone
    (removed: the DELETE), A changed (the upsert updates) and N is new (the upsert inserts)."""
    workdir = root / name
    _fetch(workdir, [_txn("A", "2026-01-10"), _txn("P", "2026-01-15", status="PENDING"),
                     _txn("K", "2026-01-15", "acc-card"), _txn("L", "2026-01-12", "acc-card")], "2026-02-01")
    scenarios.raw2cleansed(workdir)
    changed = {**_txn("A", "2026-01-10"), "amount": "-7.0"}
    _fetch(workdir, [changed, _txn("L", "2026-01-12", "acc-card"), _txn("N", "2026-01-20")], "2026-02-02")
    return workdir


def _rows(workdir: Path) -> list[dict]:
    return _delta_util.read(workdir, "cleansed", "transactions").sort("id").to_dicts()


def test_a_raw2cleansed_killed_at_any_delta_commit_is_put_right_by_the_next_run(tmp_path):
    expected = {}

    def check(workdir, step):
        if not expected:   # the dry run finished before the first check
            expected["rows"] = _rows(tmp_path / "dry")
            assert {r["id"] for r in expected["rows"]} == {"A", "L", "N"}
        # The crashed run left a table Delta can read (a commit is all or nothing).
        assert pl.read_delta(str(workdir / "cleansed" / "transactions")).height > 0, step
        scenarios.raw2cleansed(workdir)
        assert _rows(workdir) == expected["rows"], f"{step}: the next run did not put the table right"

    steps = crash_sweep.crash_sweep(f"{_SCENARIOS}:raw2cleansed",
                                    lambda name: _cleansed_before_the_second_fetch(tmp_path, name), _DELTA, check)
    assert {s.point for s in steps} == {"polars.DataFrame.write_delta", "deltalake.table.TableMerger.execute"}


# ---------------------------------------------------------------- edicao.db, in SQLite

_SQL = crash_sweep.WritePoints([f"{_SCENARIOS}.Conn.execute"], after=(f"{_SCENARIOS}.Conn.execute",))
_BEFORE = {"categorias": ["Mercado"], "mapeamento": ["Mercado"], "correcoes": ["Mercado"], "versao": 0}
_AFTER = {"categorias": ["Supermercado"], "mapeamento": ["Supermercado"], "correcoes": ["Supermercado"],
          "versao": 1}


def _edicao_with_a_category(root: Path, name: str) -> Path:
    """An edicao.db where the subcategory Mercado has a rule and a correction pointing to it."""
    workdir = root / name
    conn = scenarios.edicao.abrir(workdir / scenarios.EDICAO_DB)
    with conn:
        conn.execute("INSERT INTO categorias VALUES ('Mercado', 'Alimentação', 'Essencial')")
        conn.execute("INSERT INTO mapeamento VALUES ('padaria ficticia', 'Mercado')")
        conn.execute("INSERT INTO correcoes VALUES ('t-1', 'Mercado')")
    conn.close()
    return workdir


def _edicao_state(workdir: Path) -> dict:
    conn = scenarios.edicao.abrir(workdir / scenarios.EDICAO_DB)
    try:
        state = {t: [s for (s,) in conn.execute(f"SELECT subcategoria FROM {t} ORDER BY rowid")]  # noqa: S608 -- table names are the constants above
                 for t in ("categorias", "mapeamento", "correcoes")}
        state["versao"] = scenarios.edicao.ler_versao(conn)
    finally:
        conn.close()
    return state


def test_a_rename_killed_at_any_statement_leaves_the_whole_edit_or_nothing(tmp_path):
    def check(workdir, step):
        assert _edicao_state(tmp_path / "dry") == _AFTER
        assert _edicao_state(workdir) in (_BEFORE, _AFTER), f"{step}: a rename left half done"

    steps = crash_sweep.crash_sweep(f"{_SCENARIOS}:renomear_categoria",
                                    lambda name: _edicao_with_a_category(tmp_path, name), _SQL, check)
    assert len(steps) >= 8   # BEGIN, the checks, three UPDATEs and the version: each before and after
