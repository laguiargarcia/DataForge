import hashlib
import json
import shutil

import polars as pl
import pytest
from datetime import date
from pathlib import Path

from tests import _delta_util

# Guard: tests that assert columns added in the fatura-panel spec are Dev-workspace only until
# the changes are promoted to prod via 'dataforge deploy apply'.
# df-s65 (A5 freeze) is now PROMOTED to prod: raw2cleansed derives + freezes
# description_normalized/etl_merchant_key identically in BOTH workspaces, so the frozen-
# derivation + A10 tests below run UNCONDITIONALLY (the transition gate is retired).

TXN_COLS = ["id", "date", "amount", "category", "categoryId", "description",
            "descriptionRaw", "merchant", "accountId", "accountName", "type", "paymentData",
            "creditCardMetadata", "ingested_at"]
ACC_COLS = ["id", "type", "name", "ingested_at"]


def _write_partition(raw_root: Path, entity: str, records: list[dict],
                     cols: list[str], ingestion_date: str) -> None:
    """Write one Parquet partition mirroring write_parquet_partition output (all VARCHAR)."""
    part_dir = raw_root / entity / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / "data.parquet"
    data = {c: [(str(r.get(c)) if r.get(c) is not None else None) for r in records] for c in cols}
    pl.DataFrame(data, schema={c: pl.Utf8 for c in cols}).write_parquet(str(out))


def _seed_raw(raw_root: Path, transactions: list[dict] | None = None,
              accounts: list[dict] | None = None, ingestion_date: str = "2026-01-01",
              extra_cols: tuple = ()) -> None:
    """Write a raw Parquet partition with the post-landing2raw transaction + account shape.

    Mirrors the live raw/<entity>/ingestion_date=*/data.parquet partitions the cleansing
    step reads, plus a raw.accounts partition so account_type can be joined in.
    """
    rows = transactions if transactions is not None else [
        {"id": "txn-1", "date": "2026-01-15", "amount": "-50.0", "category": "Food",
         "description": "Coffee", "accountId": "acc-1", "accountName": "Checking",
         "ingested_at": "2026-01-01T10:00:00Z"},
        {"id": "txn-1", "date": "2026-01-15", "amount": "-50.0", "category": "Food",
         "description": "Coffee", "accountId": "acc-1", "accountName": "Checking",
         "ingested_at": "2026-01-01T11:00:00Z"},
        {"id": "txn-2", "date": "2026-01-16", "amount": "-30.0", "category": "Transport",
         "description": "Uber", "accountId": "acc-1", "accountName": "Checking",
         "ingested_at": "2026-01-01T09:00:00Z"},
    ]
    accs = accounts if accounts is not None else [{"id": "acc-1", "type": "BANK", "name": "Checking"}]
    acc_type = {a["id"]: a["type"] for a in accs}

    def _default_type(t):
        # Pluggy's sign convention, which raw2cleansed now enforces (df-ahua.3): on a BANK account
        # money in (CREDIT) is positive; on a CREDIT card a charge (DEBIT) is positive.
        positive = float(t.get("amount", "0")) > 0
        on_bank = acc_type.get(t.get("accountId", "acc-1"), "BANK") == "BANK"
        return "CREDIT" if positive == on_bank else "DEBIT"

    txn_records = [
        {
            "id": t["id"], "date": t["date"], "amount": str(t.get("amount", "0")),
            "category": t.get("category", ""), "categoryId": t.get("categoryId", ""),
            "description": t.get("description", ""), "descriptionRaw": t.get("descriptionRaw", ""),
            "merchant": t.get("merchant"), "accountId": t.get("accountId", "acc-1"),
            "accountName": t.get("accountName", "Checking"),
            "type": t.get("type", _default_type(t)), "paymentData": t.get("paymentData"),
            "creditCardMetadata": t.get("creditCardMetadata"),
            "ingested_at": t.get("ingested_at", f"{ingestion_date}T00:00:00Z"),
            **{c: t.get(c) for c in extra_cols},
        }
        for t in rows
    ]
    _write_partition(raw_root, "transactions", txn_records, TXN_COLS + list(extra_cols), ingestion_date)

    acc_records = [
        {"id": a["id"], "type": a["type"], "name": a.get("name", ""),
         "ingested_at": a.get("ingested_at", f"{ingestion_date}T00:00:00Z")}
        for a in accs
    ]
    _write_partition(raw_root, "accounts", acc_records, ACC_COLS, ingestion_date)


def _read_cleansed(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "cleansed", "transactions").sort("id").to_dicts()


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)


def test_raw2cleansed_deduplicates_by_id(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)

    rows = _read_cleansed(tmp_path)
    assert len(rows) == 2
    assert rows[0]["id"] == "txn-1"


def test_raw2cleansed_casts_amount_to_double(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)

    df = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert df.schema["amount"] == pl.Float64


def test_date_cast_to_date(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert df.schema["date"] == pl.Date


def test_date_cast_from_iso_timestamp(tmp_path):
    # Live Pluggy `date` is a full ISO timestamp (e.g. "2026-06-03T15:12:50.072Z"), not a
    # plain YYYY-MM-DD. It must truncate to the date (mirrors DuckDB CAST(... AS DATE)).
    import datetime
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "txn-ts", "date": "2026-06-03T15:12:50.072Z", "amount": "-12.0"},
    ])
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert df.schema["date"] == pl.Date
    row = df.filter(pl.col("id") == "txn-ts").to_dicts()[0]
    assert row["date"] == datetime.date(2026, 6, 3)


def test_first_run_carries_all_columns(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "A", "date": "2026-01-01", "amount": "100.0", "accountId": "acc-1",
         "categoryId": "cat-1", "descriptionRaw": "RAW DESC", "merchant": "ACME"},
    ], accounts=[{"id": "acc-1", "type": "BANK", "name": "Checking"}])
    _run(tmp_path)

    rows = _read_cleansed(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    for col in ("accountId", "account_type", "categoryId", "descriptionRaw", "merchant"):
        assert col in row, f"missing column {col}"
    assert row["account_type"] == "BANK"
    assert row["accountId"] == "acc-1"


def test_output_columns_exact(tmp_path):
    # Single canonical cleansed.transactions schema: df-pzu counterparty cols + 4 cc cols
    # (fatura-panel) + the df-s65 frozen description_normalized/etl_merchant_key, in order.
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)
    cols = _delta_util.read(tmp_path, "cleansed", "transactions").columns
    expected_cols = [
        "id", "date", "amount", "category", "categoryId", "description",
        "descriptionRaw", "merchant", "account", "accountId", "account_type",
        "counterparty_name", "counterparty_document", "counterparty_document_type",
        "payment_method",
        "cc_bill_id", "cc_installment_number", "cc_total_installments", "cc_purchase_date",
        "description_normalized", "etl_merchant_key",
    ]
    # v3 columns promoted to prod 2026-11-09 (df-668)
    expected_cols += ["status", "txn_type", "operation_type"]
    expected_cols.append("ingested_at")
    assert cols == expected_cols


def test_accumulation_refreshes_and_preserves(tmp_path):
    # Run 1: raw window has A, B (partition).
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "A", "date": "2026-01-01", "amount": "10.0", "ingested_at": "2026-01-01T00:00:00Z"},
        {"id": "B", "date": "2026-01-02", "amount": "20.0", "ingested_at": "2026-01-01T00:00:00Z"},
    ], ingestion_date="2026-01-01")
    _run(tmp_path)
    assert {r["id"] for r in _read_cleansed(tmp_path)} == {"A", "B"}

    # Run 2: SAME-day re-fetch OVERWRITES the partition — A has aged out of the Pluggy window,
    # raw now returns only B (changed) and C (new). A survives solely via the cleansed-accumulate
    # MERGE (non-matched target rows are untouched). B's amount is updated; C is inserted.
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "B", "date": "2026-01-02", "amount": "999.0", "ingested_at": "2026-01-01T12:00:00Z"},
        {"id": "C", "date": "2026-01-03", "amount": "30.0", "ingested_at": "2026-01-01T12:00:00Z"},
    ], ingestion_date="2026-01-01")
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_cleansed(tmp_path)}
    assert set(rows) == {"A", "B", "C"}, f"final table must be A,B,C; got {set(rows)}"
    assert rows["A"]["amount"] == 10.0, "A (aged out of raw) must persist with original value"
    assert rows["B"]["amount"] == 999.0, "B must be refreshed to the updated value"
    assert rows["C"]["amount"] == 30.0, "C must be inserted"


def test_account_type_joined_from_raw_accounts(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "T1", "date": "2026-01-01", "amount": "50.0", "accountId": "acc_bank"},
        {"id": "T2", "date": "2026-01-02", "amount": "80.0", "accountId": "acc_card"},
    ], accounts=[
        {"id": "acc_bank", "type": "BANK", "name": "Checking"},
        {"id": "acc_card", "type": "CREDIT", "name": "Card"},
    ])
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_cleansed(tmp_path)}
    assert rows["T1"]["account_type"] == "BANK"
    assert rows["T2"]["account_type"] == "CREDIT"


def test_empty_glob_is_noop_preserving_prior_rows(tmp_path):
    # No raw transactions partition → the glob matches nothing. Transactions ACCUMULATE, so an
    # empty fetch must be a NO-OP (pl.concat([]) would crash; an empty overwrite would wipe
    # history — A11). A pre-seeded cleansed.transactions row must survive intact.
    import datetime
    prior = pl.DataFrame(
        {
            "id": ["keep-me"],
            "date": [datetime.date(2026, 1, 1)],
            "amount": [42.0],
            "category": ["Food"], "categoryId": ["c1"], "description": ["d"],
            "descriptionRaw": ["dr"], "merchant": ["m"], "account": ["Checking"],
            "accountId": ["acc-1"], "account_type": ["BANK"],
            "ingested_at": ["2026-01-01T00:00:00Z"],
        },
        schema={
            "id": pl.Utf8, "date": pl.Date, "amount": pl.Float64, "category": pl.Utf8,
            "categoryId": pl.Utf8, "description": pl.Utf8, "descriptionRaw": pl.Utf8,
            "merchant": pl.Utf8, "account": pl.Utf8, "accountId": pl.Utf8,
            "account_type": pl.Utf8, "ingested_at": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "cleansed", "transactions", prior)

    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    _run(tmp_path)  # must not crash on the empty glob

    rows = _read_cleansed(tmp_path)
    assert len(rows) == 1, "empty fetch must be a no-op; prior cleansed row must survive"
    assert rows[0]["id"] == "keep-me"
    assert rows[0]["amount"] == 42.0


def test_no_exported_at_column(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)
    cols = set(_delta_util.read(tmp_path, "cleansed", "transactions").columns)
    assert "exported_at" not in cols, "exported_at is retired (its only consumer curated2excel is gone)"


# ── df-ahua.3.1: a purchase in another currency ──────────────────────────────────────────
# Pluggy sends the foreign value in `amount`, its currency in currencyCode and the BRL value the bank
# converted in amountInAccountCurrency (null on BRL rows). cleansed keeps the BRL value.
CURRENCY_COLS = ("currencyCode", "amountInAccountCurrency")


def _card(id_, amount, currency="BRL", converted=None, **more):
    return {"id": id_, "date": "2026-01-10", "amount": amount, "accountId": "acc-card",
            "accountName": "Card", "currencyCode": currency, "amountInAccountCurrency": converted, **more}


CARD = [{"id": "acc-card", "type": "CREDIT", "name": "Card"}]


def _amounts(tmp_path):
    return {r["id"]: r["amount"] for r in _read_cleansed(tmp_path)}


def _findings(tmp_path):
    path = tmp_path / "validation" / "raw.transactions.json"
    return [f["rule"] for f in json.loads(path.read_text(encoding="utf-8"))["findings"]]


FOREIGN_UNCONVERTED = "currencyCode is not BRL and amountInAccountCurrency is null"


def test_brl_row_keeps_its_amount_and_foreign_row_takes_the_converted_one(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("brl", "35.9"), _card("usd", "10.0", "USD", "52.5")],
              accounts=CARD, extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"brl": 35.9, "usd": 52.5}
    assert FOREIGN_UNCONVERTED not in _findings(tmp_path)


def test_foreign_row_without_the_converted_amount_warns_and_keeps_amount(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("usd", "10.0", "USD", None)],
              accounts=CARD, extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"usd": 10.0}
    assert FOREIGN_UNCONVERTED in _findings(tmp_path)


# Partitions older than the currency fields: in the combined raw with a newer one (diagonal concat
# fills the column with null), and alone (the script projects the absent column as null).
def test_partition_without_the_currency_columns(tmp_path):
    # "old" is dated before the latest fetch's window, so it is not gone from that fetch (df-75ol).
    _seed_raw(tmp_path / "raw", transactions=[_card("old", "20.0", date="2026-01-05")], accounts=CARD,
              ingestion_date="2026-01-01")
    _seed_raw(tmp_path / "raw", transactions=[_card("new", "10.0", "USD", "52.5")], accounts=CARD,
              ingestion_date="2026-01-02", extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"old": 20.0, "new": 52.5}


def test_only_partitions_without_the_currency_columns(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("old", "20.0")], accounts=CARD)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"old": 20.0}
    assert FOREIGN_UNCONVERTED not in _findings(tmp_path)


# How prod is corrected: cleansed.transactions already holds the foreign value for an id (written
# before df-ahua.3.1); the next run's MERGE updates the matched row with the converted value and leaves
# the other rows as they were.
def test_merge_corrects_a_row_cleansed_holds_with_the_foreign_amount(tmp_path):
    from deltalake import DeltaTable
    _seed_raw(tmp_path / "raw", transactions=[_card("brl", "35.9"), _card("usd", "10.0", "USD", "52.5")],
              accounts=CARD, extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    table = tmp_path / "cleansed" / "transactions"
    DeltaTable(str(table)).update(predicate="id = 'usd'", updates={"amount": "10.0"})
    before = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    assert before["amount"].to_list() == [35.9, 10.0]

    _run(tmp_path)
    after = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    assert after["amount"].to_list() == [35.9, 52.5]
    assert after.drop("amount").equals(before.drop("amount"))
    assert DeltaTable(str(table)).history(1)[0]["operation"] == "MERGE"


# ── df-75ol, rule of df-7twv: what a transaction gone from the latest fetch means ─────────
# The latest fetch (the raw partition with the greatest ingestion_date) covers, for each account in it,
# the days from its earliest date on. An id of that account dated inside that window and absent from
# that fetch is gone. It leaves cleansed on a card, and on a bank account only when it was PENDING; a
# POSTED bank row that vanishes is money that really moved, and stays. Raw stays as it was. When the
# run would remove more than GONE_MIN ids and more than GONE_PERCENT % of an account's window, the
# fetch is taken as incomplete: nothing leaves that account and the check records a suspicious finding.
GONE_CHECK_FILE = "cleansed.transactions_vs_latest_fetch.json"
GONE_RULE = "too many ids to remove from the latest fetch: none removed from the account"
STATUS = ("status",)
BANK_AND_CARD = [{"id": "acc-1", "type": "BANK"}, {"id": "acc-2", "type": "BANK"},
                 {"id": "acc-card", "type": "CREDIT"}]


def _txn(id_, day, account="acc-1", status="POSTED"):
    return {"id": id_, "date": day, "amount": "-5.0" if account != "acc-card" else "5.0",
            "accountId": account, "status": status}


def _seed_fetch(tmp_path, rows, ingestion_date):
    _seed_raw(tmp_path / "raw", rows, BANK_AND_CARD, ingestion_date, extra_cols=STATUS)


def _ids(tmp_path):
    return {r["id"] for r in _read_cleansed(tmp_path)}


def _raw_bytes(tmp_path):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((tmp_path / "raw").rglob("*")) if p.is_file()}


def _gone_findings(tmp_path):
    path = tmp_path / "validation" / GONE_CHECK_FILE
    return [f["rule"] for f in json.loads(path.read_text(encoding="utf-8"))["findings"]]


def test_gone_pending_bank_row_leaves_and_gone_posted_bank_row_stays(tmp_path, capsys):
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("P", "2026-01-15", status="PENDING"),
                           _txn("Q", "2026-01-16"), _txn("C", "2026-01-20")], "2026-02-01")
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("C", "2026-01-20")], "2026-02-02")
    raw_before = _raw_bytes(tmp_path)
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "Q", "C"}
    assert _raw_bytes(tmp_path) == raw_before
    out = capsys.readouterr().out
    assert "transactions: account acc-1: 1 ids gone from the latest fetch removed" in out
    assert _gone_findings(tmp_path) == []


def test_gone_card_row_leaves_whatever_its_status(tmp_path):
    _seed_fetch(tmp_path, [_txn("K1", "2026-01-10", "acc-card"), _txn("K2", "2026-01-15", "acc-card"),
                           _txn("K3", "2026-01-16", "acc-card", status="PENDING")], "2026-02-01")
    _seed_fetch(tmp_path, [_txn("K1", "2026-01-10", "acc-card")], "2026-02-02")
    _run(tmp_path)
    assert _ids(tmp_path) == {"K1"}


def test_gone_row_already_in_cleansed_is_deleted_and_does_not_come_back(tmp_path, capsys):
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("P", "2026-01-15", status="PENDING"),
                           _txn("K", "2026-01-15", "acc-card"), _txn("L", "2026-01-12", "acc-card")],
                "2026-02-01")
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "P", "K", "L"}

    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("L", "2026-01-12", "acc-card")], "2026-02-02")
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "L"}
    capsys.readouterr()
    _run(tmp_path)   # the next run neither puts them back nor fails, and has nothing new to say
    assert _ids(tmp_path) == {"A", "L"}
    assert "ids gone from the latest fetch removed" not in capsys.readouterr().out


def test_row_only_in_cleansed_is_judged_by_its_cleansed_state(tmp_path):
    # Ids cleansed holds but no raw partition has (raw restored from an older backup) are judged too.
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("P", "2026-01-15", status="PENDING"),
                           _txn("Q", "2026-01-16")], "2026-02-01")
    _run(tmp_path)
    shutil.rmtree(tmp_path / "raw")
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10")], "2026-02-02")
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "Q"}


def test_pending_that_posted_under_the_same_id_stays(tmp_path):
    # The state that decides is the latest one: P was PENDING, came back POSTED, then vanished.
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("P", "2026-01-15", status="PENDING")], "2026-02-01")
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10"), _txn("P", "2026-01-15")], "2026-02-02")
    _seed_fetch(tmp_path, [_txn("A", "2026-01-10")], "2026-02-03")
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "P"}


def test_id_dated_before_the_window_and_account_absent_from_the_fetch_stay(tmp_path):
    _seed_fetch(tmp_path, [_txn("OLD", "2026-01-05", "acc-card"), _txn("B", "2026-01-15", "acc-card"),
                           _txn("X", "2026-01-20", "acc-2", status="PENDING")], "2026-02-01")
    _seed_fetch(tmp_path, [_txn("B", "2026-01-15", "acc-card"), _txn("C", "2026-01-20", "acc-card")],
                "2026-02-02")
    _run(tmp_path)
    assert _ids(tmp_path) == {"OLD", "B", "C", "X"}


def test_incomplete_fetch_removes_nothing_from_the_account_and_warns(tmp_path, capsys):
    full = [_txn(f"T{n:02d}", f"2026-01-{n + 1:02d}", "acc-card") for n in range(30)]
    other = [_txn("Y1", "2026-01-10", "acc-2", "PENDING"), _txn("Y2", "2026-01-20", "acc-2", "PENDING")]
    _seed_fetch(tmp_path, full + other, "2026-02-01")
    _run(tmp_path)
    _seed_fetch(tmp_path, full[:5] + other[:1], "2026-02-02")   # 25 of 30 gone from the card
    _run(tmp_path)
    assert _ids(tmp_path) == {r["id"] for r in full} | {"Y1"}   # acc-2 still loses its one gone id
    assert _gone_findings(tmp_path) == [GONE_RULE]
    out = capsys.readouterr().out
    assert "account acc-card:" not in out
    assert "transactions: account acc-2: 1 ids gone from the latest fetch removed" in out

    _run(tmp_path)   # the same incomplete fetch tomorrow: still held, still warned
    assert _ids(tmp_path) == {r["id"] for r in full} | {"Y1"}
    assert _gone_findings(tmp_path) == [GONE_RULE]


def _gone(known_rows, latest_rows, in_cleansed):
    F = _delta_util.exec_functions({})
    known = pl.DataFrame(known_rows, schema=["id", "accountId", "date", "status", "account_type"],
                         orient="row")
    latest = pl.DataFrame(latest_rows, schema=["id", "accountId", "date"], orient="row")
    return F["gone_from_latest_fetch"](known, latest, pl.Series("id", in_cleansed, dtype=pl.Utf8))


# The guard holds an account only when BOTH limits are passed: more than 20 ids newly removed AND more
# than 10% of the account's ids inside the window. With an empty cleansed (first run, reprocess.py) every
# removal is new.
@pytest.mark.parametrize("in_window,gone,held", [
    (100, 20, False),    # 20 is not more than 20
    (100, 21, True),
    (210, 21, False),    # exactly 10%
    (209, 21, True),
    (1000, 25, False),
    (5, 4, False),       # most ids of a small account gone: below the absolute limit
])
def test_guard_needs_both_limits(in_window, gone, held):
    known = [(f"t{n}", "a", "2026-01-10", "POSTED", "CREDIT") for n in range(in_window)]
    latest = [(i, a, d) for i, a, d, _, _ in known[gone:]]
    per_account, removed = _gone(known, latest, [])
    assert per_account.rows(named=True) == [{"accountId": "a", "ids_in_window": in_window, "ids_gone": gone,
                                             "ids_newly_removed": gone, "ids_removed": 0 if held else gone}]
    assert removed.height == (0 if held else gone)


def test_guard_does_not_count_removals_already_applied():
    # 25 card ids gone, but only 3 of them are still in cleansed: the other 22 left on earlier runs.
    known = [(f"t{n}", "a", "2026-01-10", "POSTED", "CREDIT") for n in range(100)]
    latest = [(i, a, d) for i, a, d, _, _ in known[25:]]
    still_there = [f"t{n}" for n in range(22, 100)]
    per_account, removed = _gone(known, latest, still_there)
    assert per_account.select("ids_gone", "ids_newly_removed", "ids_removed").row(0) == (25, 3, 25)
    assert removed.height == 25


def test_only_card_rows_and_pending_bank_rows_are_removed():
    known = [("card-posted", "c", "2026-01-10", "POSTED", "CREDIT"),
             ("card-pending", "c", "2026-01-10", "PENDING", "CREDIT"),
             ("bank-posted", "b", "2026-01-10", "POSTED", "BANK"),
             ("bank-pending", "b", "2026-01-10", "PENDING", "BANK"),
             ("bank-no-status", "b", "2026-01-10", None, "BANK"),
             ("other-pending", "o", "2026-01-10", "PENDING", None)]
    latest = [("c-now", "c", "2026-01-10"), ("b-now", "b", "2026-01-10"), ("o-now", "o", "2026-01-10")]
    per_account, removed = _gone(known, latest, [])
    assert sorted(removed["id"].to_list()) == ["bank-pending", "card-pending", "card-posted"]
    assert dict(per_account.select("accountId", "ids_gone").rows()) == {"b": 3, "c": 2, "o": 1}


# The window is per account and starts at the earliest date (the first 10 characters, as raw holds
# full timestamps and cleansed holds dates) of that account in the latest fetch.
def test_window_is_per_account_and_reads_timestamps_and_dates():
    known = [("a-in", "a", "2026-01-12T10:00:00.000Z", "POSTED", "CREDIT"),
             ("a-edge", "a", "2026-01-10T00:00:00.000Z", "POSTED", "CREDIT"),
             ("a-before", "a", "2026-01-09T23:59:59.000Z", "POSTED", "CREDIT"),
             ("b-in", "b", "2026-01-02", "POSTED", "CREDIT"),
             ("c-in", "c", "2026-01-30", "POSTED", "CREDIT")]
    latest = [("a-now", "a", "2026-01-10T03:00:00.000Z"), ("b-now", "b", "2026-01-01")]
    _, removed = _gone(known, latest, [])
    assert sorted(removed["id"].to_list()) == ["a-edge", "a-in", "b-in"]


# ── df-pzu: paymentData -> 4 frozen counterparty columns ──────────────────────────────────
# Counterparty = the side OPPOSITE our account: receiver on a DEBIT (we paid), payer on a
# CREDIT (we were paid). NO fallback to the other side
# (the other side is our own account holder).

# ── df-mk25: a card entry Pluggy returns under two ids, and the bill proves it ───────────────────────────
# The rule (report/conferencia_fatura.py, copias_pela_fatura) runs on the card after the gone ids left: a copy
# leaves cleansed only when its bill has more entries than its total and removing it closes the bill. Raw keeps
# both ids, so the copy is judged, removed and said again on every run.
COPIES_CHECK_FILE = "cleansed.transactions_vs_bills.json"
COPIES_RULE = "card entries that look copied and the bill does not tell which: none removed"
EARLY, LATE = "2026-03-20T10:00:00.000Z", "2026-05-02T08:30:00.000Z"
CARD_COLS = ("status", "createdAt")


def _card_entry(id_, day, amount, created):
    return {"id": id_, "date": day, "amount": str(amount), "accountId": "acc-card", "status": "POSTED",
            "categoryId": "09000000", "createdAt": created, "creditCardMetadata": "{'billId': 'bill-04'}"}


A, B, C = (_card_entry("A", "2026-03-01", 10.0, EARLY), _card_entry("B", "2026-03-20", 140.0, EARLY),
           _card_entry("C", "2026-03-21", 140.0, LATE))


def _seed_card_fetch(tmp_path, rows, ingestion_date):
    _seed_raw(tmp_path / "raw", rows, BANK_AND_CARD, ingestion_date, extra_cols=CARD_COLS)


def _seed_bill(tmp_path, total=150.0):
    _delta_util.seed(tmp_path, "cleansed", "bills", pl.DataFrame({
        "id": ["bill-04"], "accountId": ["acc-card"], "dueDate": ["2026-04-17T00:00:00.000Z"],
        "totalAmount": [total], "updatedAt": ["2026-04-10T00:00:00.000Z"]}))


def _copies_findings(tmp_path):
    path = tmp_path / "validation" / COPIES_CHECK_FILE
    return [(f["rule"], f["keys"]) for f in json.loads(path.read_text(encoding="utf-8"))["findings"]]


KEPT_LINE = "transactions: card entry copied under two ids, the bill settles it: kept B, removed C"


def test_copy_the_bill_proves_leaves_cleansed_on_every_run(tmp_path, capsys):
    _seed_card_fetch(tmp_path, [A, B, C], "2026-04-20")
    _seed_bill(tmp_path)
    raw_before = _raw_bytes(tmp_path)
    for _ in range(2):   # raw still has C: the next run removes it again, and says it
        _run(tmp_path)
        assert _ids(tmp_path) == {"A", "B"}
        assert KEPT_LINE in capsys.readouterr().out
        assert _copies_findings(tmp_path) == []
    assert _raw_bytes(tmp_path) == raw_before


def test_copy_already_in_cleansed_is_deleted(tmp_path):
    _seed_card_fetch(tmp_path, [A, B, C], "2026-04-20")
    _run(tmp_path)   # no cleansed.bills yet: the rule has no judge and removes nothing
    assert _ids(tmp_path) == {"A", "B", "C"}
    _seed_bill(tmp_path)
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "B"}


def test_a_pair_whose_first_id_is_gone_keeps_the_second(tmp_path):
    # B stops coming back: the gone rule removes it before the copy rule runs, C is alone and stays. In the
    # other order the purchase would be lost twice (C as the copy, B as gone).
    _seed_bill(tmp_path)
    _seed_card_fetch(tmp_path, [A, B, C], "2026-04-20")
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "B"}
    _seed_card_fetch(tmp_path, [A, C], "2026-04-21")
    for _ in range(2):
        _run(tmp_path)
        assert _ids(tmp_path) == {"A", "C"}


def test_bill_that_cannot_tell_which_keeps_everything_and_warns(tmp_path, capsys):
    _seed_card_fetch(tmp_path, [A, B, {**C, "createdAt": EARLY}], "2026-04-20")
    _seed_bill(tmp_path)
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "B", "C"}
    assert _copies_findings(tmp_path) == [(COPIES_RULE, ["bill-04"])]
    assert "card entry copied" not in capsys.readouterr().out


def test_repeated_purchase_in_a_bill_that_closes_stays(tmp_path):
    _seed_card_fetch(tmp_path, [A, B, C], "2026-04-20")
    _seed_bill(tmp_path, total=290.0)
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "B", "C"}
    assert _copies_findings(tmp_path) == []


def test_bank_rows_are_never_judged_by_a_bill(tmp_path):
    bank = [{**r, "accountId": "acc-1", "amount": str(-float(r["amount"]))} for r in (A, B, C)]
    _seed_card_fetch(tmp_path, bank, "2026-04-20")
    _seed_bill(tmp_path)
    _run(tmp_path)
    assert _ids(tmp_path) == {"A", "B", "C"}


def _cp_row(tmp_path, txn_type, payment_data):
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "T", "date": "2026-01-01", "amount": "-5.0" if txn_type == "DEBIT" else "5.0",
         "type": txn_type,
         "paymentData": payment_data, "accountId": "acc-1"},
    ], accounts=[{"id": "acc-1", "type": "BANK"}])
    _run(tmp_path)
    return _read_cleansed(tmp_path)[0]


def test_paymentdata_debit_uses_receiver(tmp_path):
    row = _cp_row(tmp_path, "DEBIT", {
        "paymentMethod": "PIX",
        "receiver": {"name": "IFOOD", "documentNumber": {"type": "CNPJ", "value": "11.222.333/0001-44"}},
        "payer": {"name": None, "documentNumber": {"type": "CPF", "value": "314.926.589-32"}},
    })
    assert row["counterparty_name"] == "IFOOD"
    assert row["counterparty_document"] == "11.222.333/0001-44"
    assert row["counterparty_document_type"] == "PJ"
    assert row["payment_method"] == "PIX"


def test_paymentdata_credit_uses_payer(tmp_path):
    row = _cp_row(tmp_path, "CREDIT", {
        "paymentMethod": "TED",
        "receiver": {"name": None, "documentNumber": {"type": "CPF", "value": "314.926.589-32"}},
        "payer": {"name": "CONTRAPARTE EXEMPLO", "documentNumber": {"type": "CNPJ", "value": "11.445.778/0001-99"}},
    })
    assert row["counterparty_name"] == "CONTRAPARTE EXEMPLO"
    assert row["counterparty_document"] == "11.445.778/0001-99"
    assert row["counterparty_document_type"] == "PJ"
    assert row["payment_method"] == "TED"


def test_paymentdata_cpf_maps_to_pf_name_may_be_null(tmp_path):
    # Person counterparties carry a CPF but Pluggy exposes no name (PII) — name stays null.
    row = _cp_row(tmp_path, "DEBIT", {
        "paymentMethod": "PIX",
        "receiver": {"name": None, "documentNumber": {"type": "CPF", "value": "626.383.950-12"}},
    })
    assert row["counterparty_document"] == "626.383.950-12"
    assert row["counterparty_document_type"] == "PF"
    assert row["counterparty_name"] is None


def test_paymentdata_no_fallback_to_account_holder(tmp_path):
    # DEBIT with the chosen side (receiver) absent: must NOT fall back to payer (our own
    # account holder). The locked acceptance counts of counterparty CPFs depend on this.
    row = _cp_row(tmp_path, "DEBIT", {
        "paymentMethod": "OTHER",
        "payer": {"name": None, "documentNumber": {"type": "CPF", "value": "314.926.589-32"}},
    })
    assert row["counterparty_document"] is None, "must not borrow the account holder's own document"
    assert row["counterparty_name"] is None
    assert row["counterparty_document_type"] is None
    assert row["payment_method"] == "OTHER"


def test_paymentdata_none_or_malformed_yields_all_null(tmp_path):
    none_row = _cp_row(tmp_path, "DEBIT", None)  # stored as the literal "None" / null
    for col in ("counterparty_name", "counterparty_document", "counterparty_document_type", "payment_method"):
        assert none_row[col] is None

    bad_row = _cp_row(tmp_path, "DEBIT", "{not a dict")  # malformed repr
    for col in ("counterparty_name", "counterparty_document", "counterparty_document_type", "payment_method"):
        assert bad_row[col] is None


def test_payment_method_extracted_without_counterparty(tmp_path):
    # paymentMethod is a per-transaction attribute populated even when no counterparty doc exists.
    row = _cp_row(tmp_path, "DEBIT", {"paymentMethod": "DEBIT", "receiver": None, "payer": None})
    assert row["payment_method"] == "DEBIT"
    assert row["counterparty_document"] is None


# ── fatura-panel spec: creditCardMetadata -> 4 frozen cc columns ──────────────────────────


def test_cc_metadata_parsed_to_four_columns(tmp_path):
    _seed_raw(tmp_path / "raw", [
        {"id": "t1", "date": "2026-05-19", "amount": "463.85", "accountId": "a1",
         "creditCardMetadata": "{'cardNumber': '7781', 'billId': 'b-123'}"},
        {"id": "t2", "date": "2025-05-11", "amount": "21.7", "accountId": "a1",
         "creditCardMetadata": ("{'cardNumber': '4821', 'purchaseDate': '2025-05-11T03:00:00.000Z', "
                                "'totalInstallments': 12, 'installmentNumber': 4, 'billId': 'b-77'}")},
    ], accounts=[{"id": "a1", "type": "CREDIT"}])
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    assert out.get_column("cc_bill_id").to_list() == ["b-123", "b-77"]
    assert out.get_column("cc_installment_number").to_list() == [None, 4]
    assert out.get_column("cc_total_installments").to_list() == [None, 12]
    assert out.get_column("cc_purchase_date").to_list() == [None, date(2025, 5, 11)]


def test_cc_metadata_null_none_or_malformed_yields_all_null(tmp_path):
    _seed_raw(tmp_path / "raw", [
        {"id": "t1", "date": "2026-05-01", "amount": "1.0", "accountId": "a1",
         "creditCardMetadata": None},
        {"id": "t2", "date": "2026-05-02", "amount": "2.0", "accountId": "a1",
         "creditCardMetadata": "None"},
        {"id": "t3", "date": "2026-05-03", "amount": "3.0", "accountId": "a1",
         "creditCardMetadata": "{broken"},
    ], accounts=[{"id": "a1", "type": "CREDIT"}])
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions")
    for col in ("cc_bill_id", "cc_installment_number", "cc_total_installments", "cc_purchase_date"):
        assert out.get_column(col).null_count() == 3


def test_cc_metadata_column_absent_from_raw_projects_null(tmp_path):
    # Simulates reprocess over partitions that predate creditCardMetadata capture:
    # seed WITHOUT the column (build the partition with the pre-change TXN_COLS).
    cols_without = [c for c in TXN_COLS if c != "creditCardMetadata"]
    _write_partition(tmp_path / "raw", "transactions",
                     [{"id": "t1", "date": "2026-05-01", "amount": "1.0", "accountId": "a1",
                       "ingested_at": "2026-01-01"}],
                     cols_without, "2026-01-01")
    _write_partition(tmp_path / "raw", "accounts",
                     [{"id": "a1", "type": "CREDIT", "ingested_at": "2026-01-01"}], ACC_COLS,
                     "2026-01-01")
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert out.get_column("cc_bill_id").to_list() == [None]


# ── df-s65 (A5 freeze) + df-7sa: description_normalized + etl_merchant_key frozen at cleansed ──
# Derivation moved raw->cleansed (was curated). These PORT the value scenarios that used to
# live in test_cleansed2curated.py. Frozen with institution=None (match-all) — identical output
# to the old curated derivation under today's un-scoped config.

def _frozen_row(tmp_path, **txn):
    txn.setdefault("id", "A")
    txn.setdefault("date", "2026-01-01")
    txn.setdefault("amount", "5.0")
    txn.setdefault("accountId", "acc-1")
    _seed_raw(tmp_path / "raw", transactions=[txn],
              accounts=[{"id": "acc-1", "type": "BANK"}])
    _run(tmp_path)
    return _read_cleansed(tmp_path)[0]


@pytest.mark.parametrize("description,expected", [
    ("ELCSS MERCADO FICT0101", "MERCADO FICT"),
    ("ELCSS PADARIA  XYZ 0101", "PADARIA XYZ"),
    ("ELCSS SORVETERIA X0202", "SORVETERIA X"),
    ("ELCSS FARMACIA EXE0202", "FARMACIA EXE"),
])
def test_frozen_etl_merchant_key_debit_pos_rule_strips_prefix_and_ddmm(tmp_path, description, expected):
    # Uses the shipped config (itau-debit-pos rule, no institution: => match-all). With no
    # Pluggy merchant, the key IS the normalized description.
    row = _frozen_row(tmp_path, description=description)
    assert row["etl_merchant_key"] == expected
    assert row["description_normalized"] == expected


def test_frozen_etl_merchant_key_prefers_plain_pluggy_merchant(tmp_path):
    # A plain-string Pluggy merchant wins; description_normalized is still from the description.
    row = _frozen_row(tmp_path, description="ELCSS WHATEVER  0101", merchant="Padaria Real")
    assert row["etl_merchant_key"] == "Padaria Real"
    assert row["description_normalized"] == "WHATEVER"


def test_frozen_etl_merchant_key_prefers_counterparty_name_over_description(tmp_path):
    # No Pluggy merchant: the frozen counterparty_name (from paymentData) becomes the key.
    row = _frozen_row(tmp_path, description="ELCSS WHATEVER  0101", merchant=None,
                      type="DEBIT", amount="-5.0", paymentData={
                          "paymentMethod": "PIX",
                          "receiver": {"name": "RESTAURANTE EXEMPLO ONLINE LTDA",
                                       "documentNumber": {"type": "CNPJ", "value": "11.222.333/0001-44"}},
                      })
    assert row["counterparty_name"] == "RESTAURANTE EXEMPLO ONLINE LTDA"
    assert row["etl_merchant_key"] == "RESTAURANTE EXEMPLO ONLINE LTDA"
    assert row["description_normalized"] == "WHATEVER", "normalized string still from description"


def test_frozen_etl_merchant_key_unpacks_nested_merchant_repr_string(tmp_path):
    # df-7sa: landing str()-ifies a nested Pluggy merchant object into a repr-string. The
    # frozen key must extract the merchant NAME (preferred over businessName), not the repr.
    row = _frozen_row(tmp_path, description="x",
                      merchant="{'name': 'ACME', 'businessName': 'ACME LTDA'}")
    assert row["etl_merchant_key"] == "ACME", "name preferred over businessName; repr-string unpacked"


def test_frozen_etl_merchant_key_empty_merchant_falls_through_to_description(tmp_path):
    # M5: merchant == "" (empty string) must fall through to description_normalized.
    row = _frozen_row(tmp_path, description="ELCSS MERCADO FICT0101", merchant="")
    assert row["etl_merchant_key"] == "MERCADO FICT"
    assert row["description_normalized"] == "MERCADO FICT"


def test_frozen_description_normalized_collapses_whitespace_for_unmatched(tmp_path):
    row = _frozen_row(tmp_path, description="Pix enviado   Fulano  Exemplo Silva")
    assert row["etl_merchant_key"] == "Pix enviado Fulano Exemplo Silva"
    assert row["description_normalized"] == "Pix enviado Fulano Exemplo Silva"


def test_frozen_etl_merchant_key_prefers_merchant_over_counterparty_name(tmp_path):
    # Precedence (df-s65, ported from test_cleansed2curated): when BOTH a structured Pluggy
    # merchant AND a counterparty_name are present, the merchant wins (not just merchant-vs-
    # description). Pairs with the merchant-vs-description and counterparty-vs-description cases.
    row = _frozen_row(tmp_path, description="ELCSS WHATEVER  0101", merchant="Padaria Real",
                      type="DEBIT", amount="-5.0", paymentData={
                          "paymentMethod": "PIX",
                          "receiver": {"name": "IFOOD",
                                       "documentNumber": {"type": "CNPJ", "value": "11.222.333/0001-44"}},
                      })
    assert row["counterparty_name"] == "IFOOD"
    assert row["etl_merchant_key"] == "Padaria Real", "structured merchant beats counterparty_name"


def test_frozen_etl_merchant_key_extensible_to_another_bank_via_config(tmp_path, monkeypatch):
    # Config-driven multi-institution support (df-s65, ported): a different bank's description
    # format normalizes with only a config change, no code change. (Rule un-scoped => match-all.)
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(
        "rules:\n"
        "  - name: bankx\n"
        "    match: '^TXN '\n"
        "    strip:\n"
        "      - '^TXN '\n"
        "      - ' #\\d+$'\n"
    )
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    row = _frozen_row(tmp_path, description="TXN PADARIA CENTRAL #0042")
    assert row["etl_merchant_key"] == "PADARIA CENTRAL"


# ── A10 institution-scoped normalization (df-s65) ─────────────────────────────────────────
# raw2cleansed still carries the full A10 machinery: _rule_applies_to + an institution-aware
# _normalize_description(description, institution=None). The live freeze derives with
# institution=None (match-all) because the shipped config's sole rule is un-scoped
# (df-s65 user-approved decision), so the scoping is NOT exercised by the pipeline — but the
# helpers accept `institution`, so we unit-test the scoping directly. NOTE: the frozen
# _merchant_key has NO institution parameter (3-arg, derives via _normalize_description with
# institution=None), so scoping is tested on _normalize_description / _rule_applies_to.

def _load_norm_funcs(tmp_path, monkeypatch, rules_yaml: str) -> dict:
    """Exec only raw2cleansed.py up to the argparse line so we get _normalize_description /
    _rule_applies_to / _merchant_key bound against a chosen rules config, without running the
    pipeline. The A10 institution machinery lives in raw2cleansed since df-s65."""
    scripts = _delta_util.SCRIPTS
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(rules_yaml, encoding="utf-8")
    script = scripts / "raw2cleansed" / "raw2cleansed.py"
    # keep everything before argument parsing (the pure helpers + rule load)
    head = _delta_util.compile_script(script, until="parser = argparse.ArgumentParser()")
    ns: dict = {"__file__": str(script)}
    _delta_util.exec_functions(ns)
    # monkeypatch (not raw os.environ) so MERCHANT_NORM_CONFIG is restored after the test —
    # otherwise it leaks into later tests that load the live config (order-dependent failures).
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    exec(head, ns)
    return ns


def test_a10_unscoped_rule_is_institution_invariant(tmp_path, monkeypatch):
    # The shipped itau-debit-pos rule has NO institution: key. Its output must be identical for
    # institution=None, "Banco Fictício", and any other institution — proving A10 adds zero
    # output change for the current (un-scoped) config, consistent with the institution=None freeze.
    ns = _load_norm_funcs(tmp_path, monkeypatch,
        "rules:\n"
        "  - name: debit-pos-prefix\n"
        "    match: '^ELCSS '\n"
        "    strip:\n"
        "      - '^ELCSS '\n"
        "      - '\\d{4}$'\n"
    )
    nd = ns["_normalize_description"]
    desc = "ELCSS MERCADO FICT0101"
    baseline = nd(desc, None)            # (description, institution)
    assert baseline == "MERCADO FICT"
    assert nd(desc, "Banco Fictício") == baseline
    assert nd(desc, "Nubank") == baseline
    assert nd(desc) == baseline          # institution default => None => match-all
    # And the frozen 3-arg _merchant_key (no institution param) derives the same value.
    assert ns["_merchant_key"](desc, None, None) == baseline


def test_a10_scoped_rule_applies_only_to_its_institution(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch,
        "rules:\n"
        "  - name: nubank-only\n"
        "    match: '^NU '\n"
        "    institution: Nubank\n"
        "    strip:\n"
        "      - '^NU '\n"
    )
    nd = ns["_normalize_description"]
    desc = "NU MERCADO X"
    # institution matches the rule's scope => rule applies (prefix stripped)
    assert nd(desc, "Nubank") == "MERCADO X"
    # institution does NOT match => rule skipped => only whitespace-collapse remains
    assert nd(desc, "Banco Fictício") == "NU MERCADO X"
    # institution=None => scoped rule is NOT eligible (None means "no institution known")
    assert nd(desc, None) == "NU MERCADO X"


def test_a10_scoped_rule_accepts_institution_list(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch,
        "rules:\n"
        "  - name: shared\n"
        "    match: '^PIX '\n"
        "    institution: [Banco Fictício, Nubank]\n"
        "    strip:\n"
        "      - '^PIX '\n"
    )
    nd = ns["_normalize_description"]
    desc = "PIX FULANO"
    assert nd(desc, "Banco Fictício") == "FULANO"
    assert nd(desc, "Nubank") == "FULANO"
    assert nd(desc, "Banco Modelo") == "PIX FULANO"  # not in list => skipped
