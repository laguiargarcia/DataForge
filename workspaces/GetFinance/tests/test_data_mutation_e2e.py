"""Data mutation end to end on the highest-risk path (ADR 0004, df-ahua.4): raw2cleansed.py, the
script that MERGEs into the one accumulating cleansed table, run as a subprocess the way the job runs
it, over a temp workspace built from generated raw partitions (made-up values only).

A good run writes cleansed.transactions first. Then each scenario lands one more raw partition and
runs the script again:
- a corruption the checks must reject: the script exits non-zero before it writes, and every file of
  the cleansed table (data and Delta log) is byte for byte what the good run left;
- a corruption the pipeline must tolerate: the script succeeds and the table holds the same rows.

The scenarios run in parallel (a few seconds each on a slow disk).
"""
import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import polars as pl
import pytest

from tests import _delta_util
from tests.test_data_mutation import D1, D2, RAW_ACCOUNT_ROWS, RAW_TRANSACTION_ROWS, raw_transaction

D3 = "2026-01-12"
WORKERS = 4


def _partition(ws, entity, rows, day):
    part = ws / "data" / "raw" / entity / f"ingestion_date={day}"
    part.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(c for r in rows for c in r))
    df = pl.DataFrame({c: [r.get(c) for r in rows] for c in cols}, schema={c: pl.Utf8 for c in cols})
    df.write_parquet(part / "data.parquet")
    return part / "data.parquet"


def _workspace(root):
    # Only what raw2cleansed.py reads: functions.py, the script, the bill check its copy rule imports
    # (df-mk25), the connections, a merchant config.
    ws = root / "ws"
    for rel in ("functions/functions.py", "raw2cleansed/raw2cleansed.py", "report/conferencia_fatura.py"):
        (ws / "scripts" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_delta_util.SCRIPTS / rel, ws / "scripts" / rel)
    shutil.copytree(_delta_util.ROOT / "connections", ws / "connections")
    (ws / "config").mkdir()
    (ws / "config" / "merchant_normalization.yaml").write_bytes(b"rules: []\n")
    (ws / ".env").write_bytes(b"")
    for day in (D1, D2):
        _partition(ws, "accounts", [r for r in RAW_ACCOUNT_ROWS if r["ingested_at"] == day], day)
        _partition(ws, "transactions", [r for r in RAW_TRANSACTION_ROWS if r["ingested_at"] == day], day)
    return ws


def _run(ws):
    return subprocess.run([sys.executable, str(ws / "scripts" / "raw2cleansed" / "raw2cleansed.py"),
                           "--source", "raw", "--target", "cleansed"],
                          cwd=ws, capture_output=True, text=True, encoding="utf-8", check=False)


def _table_bytes(ws):
    table = ws / "data" / "cleansed" / "transactions"
    return {str(p.relative_to(table)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(table.rglob("*")) if p.is_file()}


def _table_rows(ws):
    df = pl.read_delta(str(ws / "data" / "cleansed" / "transactions"))
    return df.sort("id")


def _new_txn(**changes):
    return raw_transaction(("t4", 9, "-7.25", "DEBIT", "acc0", D3), **changes)


def _null_id(ws):
    _partition(ws, "transactions", [_new_txn(id=None)], D3)


def _amount_dropped(ws):
    # Pluggy stopped sending `amount` today: the older partitions still have the column.
    row = _new_txn()
    del row["amount"]
    _partition(ws, "transactions", [row], D3)


def _unparseable_amount(ws):
    _partition(ws, "transactions", [_new_txn(amount="1.234,56")], D3)


def _flipped_sign(ws):
    _partition(ws, "transactions", [_new_txn(amount="7.25")], D3)  # a BANK debit must be negative


def _ingested_at_not_a_day(ws):
    _partition(ws, "transactions", [_new_txn(ingested_at="n/a")], D3)


def _truncated_file(ws):
    path = _partition(ws, "transactions", [_new_txn()], D3)
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


def _partition_copied(ws):
    # A copy of the latest partition under another day: its ingested_at is not that day (A16), and a
    # later copy would win latest_by_key over the fetches in between (df-ahua.4.1).
    src = ws / "data" / "raw" / "transactions" / f"ingestion_date={D2}"
    shutil.copytree(src, src.parent / f"ingestion_date={D3}")


def _rows_repeated(ws):
    # The latest fetch holds each of its rows twice, identical: latest_by_key keeps one of two equal
    # rows, so nothing changes (df-ahua.4.3 refuses only copies that differ).
    path = ws / "data" / "raw" / "transactions" / f"ingestion_date={D2}" / "data.parquet"
    rows = pl.read_parquet(path)
    pl.concat([rows, rows]).write_parquet(path)


def _id_twice_in_a_fetch(ws):
    _partition(ws, "transactions", [_new_txn(), _new_txn(amount="-8.25")], D3)


def _foreign(converted):
    # A purchase in another currency (df-ahua.3.1): amount is the foreign value, amountInAccountCurrency
    # the BRL one cleansed takes.
    return _new_txn(currencyCode="USD", amountInAccountCurrency=converted)


def _converted_amount_unparseable(ws):
    _partition(ws, "transactions", [_foreign("38,06")], D3)


def _converted_amount_flipped(ws):
    _partition(ws, "transactions", [_foreign("38.06")], D3)  # a BANK debit must be negative


def _converted_amount_missing(ws):
    _partition(ws, "transactions", [_foreign(None)], D3)


# (name, corruption, expected: the text the failure names, or None when it must be tolerated)
SCENARIOS = [
    ("null id", _null_id, "raw.transactions: id is null"),
    ("amount column dropped today", _amount_dropped, "raw.transactions: amount is missing or not a number"),
    ("amount that does not parse", _unparseable_amount, "raw.transactions: amount is missing or not a number"),
    ("flipped sign", _flipped_sign, "cleansed.transactions: amount sign contradicts"),
    ("ingested_at that is not a day", _ingested_at_not_a_day, "raw.transactions: ingested_at is not a date"),
    ("truncated parquet file", _truncated_file, "parquet"),
    ("partition copied under another day", _partition_copied,
     "raw.transactions: ingested_at is not its partition's day"),
    ("rows repeated within a fetch", _rows_repeated, None),
    ("converted amount that does not parse", _converted_amount_unparseable,
     "raw.transactions: amountInAccountCurrency is not a number"),
    ("converted amount with a flipped sign", _converted_amount_flipped,
     "cleansed.transactions: amount sign contradicts"),
]
# Accepted with a warning: the run writes, the finding is recorded for data_warnings.
WARNED = [("same id twice in one fetch", _id_twice_in_a_fetch,
           "id appears twice in one fetch with different content"),
          ("foreign currency without the converted amount", _converted_amount_missing,
           "currencyCode is not BRL and amountInAccountCurrency is null")]


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    base = _workspace(tmp_path_factory.mktemp("good"))
    good = _run(base)
    assert good.returncode == 0, good.stderr
    before_bytes, before_rows = _table_bytes(base), _table_rows(base)

    def scenario(item):
        name, corrupt, _ = item
        ws = tmp_path_factory.mktemp("scenario") / "ws"
        shutil.copytree(base, ws)
        corrupt(ws)
        return name, _run(ws), ws

    with ThreadPoolExecutor(WORKERS) as pool:
        runs = {name: (proc, ws) for name, proc, ws in pool.map(scenario, SCENARIOS + WARNED)}
    return before_bytes, before_rows, runs


def test_the_good_run_writes_every_raw_id(results):
    _, rows, _ = results
    assert rows["id"].to_list() == ["t0", "t1", "t2", "t3", "t5"]


# df-ahua.3.1: the foreign-currency row is written with the BRL value the bank converted, the BRL rows
# with their own amount.
def test_the_good_run_takes_the_converted_amount(results):
    _, rows, _ = results
    raw = {r["id"]: r for r in RAW_TRANSACTION_ROWS}
    for row in rows.iter_rows(named=True):
        assert row["amount"] == float(raw[row["id"]]["amountInAccountCurrency"] or raw[row["id"]]["amount"])
    assert rows.filter(pl.col("id") == "t5")["amount"].item() == 52.5


@pytest.mark.parametrize("name,expected", [(s[0], s[2]) for s in SCENARIOS if s[2]],
                         ids=[s[0] for s in SCENARIOS if s[2]])
def test_corrupt_raw_stops_the_script_before_it_writes(results, name, expected):
    before_bytes, _, runs = results
    proc, ws = runs[name]
    assert proc.returncode != 0
    assert expected.lower() in proc.stderr.lower()
    assert _table_bytes(ws) == before_bytes


@pytest.mark.parametrize("name", [s[0] for s in SCENARIOS if s[2] is None])
def test_tolerated_corruption_leaves_the_same_rows(results, name):
    _, before_rows, runs = results
    proc, ws = runs[name]
    assert proc.returncode == 0, proc.stderr
    assert _table_rows(ws).equals(before_rows)


# The same id twice in one fetch with different content is suspicious, not impossible: the run writes,
# the finding is in the raw.transactions findings file, and cleansed holds that id once.
@pytest.mark.parametrize("name,rule", [(w[0], w[2]) for w in WARNED], ids=[w[0] for w in WARNED])
def test_suspicious_raw_is_written_with_a_warning(results, name, rule):
    _, before_rows, runs = results
    proc, ws = runs[name]
    assert proc.returncode == 0, proc.stderr
    findings = json.loads((ws / "data" / "validation" / "raw.transactions.json").read_text(encoding="utf-8"))
    assert [f["rule"] for f in findings["findings"] if f["rule"] == rule] == [rule]
    rows = _table_rows(ws)
    assert rows.filter(pl.col("id") == "t4").height == 1
    assert rows.filter(pl.col("id") != "t4").equals(before_rows)
