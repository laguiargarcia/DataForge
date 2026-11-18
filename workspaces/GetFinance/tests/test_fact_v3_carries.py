# df-668.1: raw2cleansed carries status / txn_type / operation_type into cleansed.
import polars as pl
import pytest
from pathlib import Path

from tests import _delta_util


_RAW_TXN_DEFAULTS = {
    "description": "PIX X", "descriptionRaw": None, "currencyCode": "BRL",
    "amountInAccountCurrency": None, "category": "Groceries", "categoryId": "19000000",
    "balance": None, "providerCode": None, "paymentData": "None", "creditCardMetadata": "None",
    "acquirerData": None, "merchant": None, "providerId": None, "order": None,
    "createdAt": None, "updatedAt": None, "accountName": "Conta", "accountId": "acc-bank",
    "status": "POSTED", "type": "DEBIT", "operationType": "PIX",
}


def _seed_raw(tmp_path: Path, txns: list[dict], drop_cols: tuple[str, ...] = ()) -> None:
    rows = [{**_RAW_TXN_DEFAULTS, **t} for t in txns]
    df = pl.DataFrame(rows).with_columns(pl.all().cast(pl.Utf8, strict=False))
    if drop_cols:
        df = df.drop(list(drop_cols))
    part = tmp_path / "raw" / "transactions" / "ingestion_date=2026-07-01"
    part.mkdir(parents=True)
    df.write_parquet(part / "data.parquet")
    acc = pl.DataFrame({"id": ["acc-bank"], "type": ["BANK"], "ingested_at": ["2026-07-01T00:00:00"]})
    apart = tmp_path / "raw" / "accounts" / "ingestion_date=2026-07-01"
    apart.mkdir(parents=True)
    acc.write_parquet(apart / "data.parquet")


def _run(tmp_path):
    return _delta_util.run_script(
        "raw2cleansed", "raw2cleansed", ["--source", "raw", "--target", "cleansed"], tmp_path
    )


def test_carries_status_txn_type_operation_type(tmp_path):
    _seed_raw(tmp_path, [
        {"id": "t1", "date": "2026-06-05", "amount": "-50.0", "ingested_at": "2026-07-01T00:00:00",
         "status": "POSTED", "type": "DEBIT", "operationType": "PIX"},
        {"id": "t2", "date": "2026-06-06", "amount": "100.0", "ingested_at": "2026-07-01T00:00:00",
         "status": "PENDING", "type": "CREDIT", "operationType": "TED"},
    ])
    _run(tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    for col in ("status", "txn_type", "operation_type"):
        assert col in out.columns, f"missing carried column {col}"
    assert out["status"].to_list() == ["POSTED", "PENDING"]
    assert out["txn_type"].to_list() == ["DEBIT", "CREDIT"]
    assert out["operation_type"].to_list() == ["PIX", "TED"]


def test_old_partition_without_new_raw_columns_projects_null(tmp_path):
    # Pre-carry raw partitions lack status/type/operationType entirely — the defensive
    # projection guard must emit typed NULLs, not crash (reprocess over full history).
    _seed_raw(
        tmp_path,
        [{"id": "t1", "date": "2026-06-05", "amount": "-50.0", "ingested_at": "2026-07-01T00:00:00"}],
        drop_cols=("status", "type", "operationType"),
    )
    _run(tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert out["status"].to_list() == [None]
    assert out["txn_type"].to_list() == [None]
    assert out["operation_type"].to_list() == [None]


_CLEANSED_DEFAULTS = {
    "date": "2026-06-05", "amount": -50.0, "category": "Groceries", "categoryId": "19000000",
    "description": "X", "descriptionRaw": None, "merchant": None, "account": "Conta",
    "accountId": "acc-bank", "account_type": "BANK", "counterparty_name": None,
    "counterparty_document": None, "counterparty_document_type": None, "payment_method": None,
    "cc_bill_id": None, "cc_installment_number": None, "cc_total_installments": None,
    "cc_purchase_date": None, "description_normalized": "X", "etl_merchant_key": "X",
    "ingested_at": "2026-07-01T00:00:00", "status": "POSTED", "txn_type": "DEBIT",
    "operation_type": "PIX",
}
_CLEANSED_SCHEMA = {
    "id": pl.Utf8, "date": pl.Utf8, "amount": pl.Float64, "category": pl.Utf8,
    "categoryId": pl.Utf8, "description": pl.Utf8, "descriptionRaw": pl.Utf8,
    "merchant": pl.Utf8, "account": pl.Utf8, "accountId": pl.Utf8, "account_type": pl.Utf8,
    "counterparty_name": pl.Utf8, "counterparty_document": pl.Utf8,
    "counterparty_document_type": pl.Utf8, "payment_method": pl.Utf8, "cc_bill_id": pl.Utf8,
    "cc_installment_number": pl.Int64, "cc_total_installments": pl.Int64,
    "cc_purchase_date": pl.Date, "description_normalized": pl.Utf8, "etl_merchant_key": pl.Utf8,
    "ingested_at": pl.Utf8, "status": pl.Utf8, "txn_type": pl.Utf8, "operation_type": pl.Utf8,
}


def _seed_cleansed(tmp_path, txns: list[dict]):
    rows = [{**_CLEANSED_DEFAULTS, **t} for t in txns]
    df = pl.DataFrame(
        {c: [r.get(c) for r in rows] for c in _CLEANSED_SCHEMA}, schema=_CLEANSED_SCHEMA
    ).with_columns(pl.col("date").str.to_date("%Y-%m-%d"))
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)
    dim = pl.DataFrame({
        "account_uuid": ["u-bank", "u-card"],
        "pluggy_account_id": ["acc-bank", "acc-card"],
    })
    _delta_util.seed(tmp_path, "curated", "dim_account", dim)


def _run_c2c(tmp_path):
    return _delta_util.run_script(
        "cleansed2curated", "cleansed2curated",
        ["--source", "cleansed", "--target", "curated"], tmp_path,
    )


def _fact(tmp_path):
    return _delta_util.read(tmp_path, "curated", "fact_transaction").sort("id")


def test_fact_carries_and_cash_flag(tmp_path):
    _seed_cleansed(tmp_path, [
        {"id": "bank-posted", "account_type": "BANK", "status": "POSTED",
         "txn_type": "DEBIT", "operation_type": "PIX"},
        {"id": "bank-pending", "account_type": "BANK", "status": "PENDING",
         "txn_type": "DEBIT", "operation_type": "PIX"},
        {"id": "card-posted", "accountId": "acc-card", "account_type": "CREDIT",
         "amount": 30.0, "status": "POSTED", "txn_type": "DEBIT", "operation_type": None},
    ])
    _run_c2c(tmp_path)
    out = _fact(tmp_path)
    for col in ("status", "txn_type", "operation_type", "cash_flag"):
        assert col in out.columns, f"missing {col}"
    by = {r["id"]: r for r in out.iter_rows(named=True)}
    assert by["bank-posted"]["cash_flag"] is True          # E4: BANK + POSTED
    assert by["bank-pending"]["cash_flag"] is False        # E4: pending never cash
    assert by["card-posted"]["cash_flag"] is False         # card purchases are debt, not cash
    assert by["bank-posted"]["txn_type"] == "DEBIT"
    assert by["card-posted"]["operation_type"] is None


def test_c2c_fails_fast_without_v3_carried_columns(tmp_path):
    # df-668.1 fail-fast guard: a cleansed table that predates the v3 carry (missing
    # status/txn_type/operation_type) must raise, not silently derive garbage. Built
    # straight from _CLEANSED_SCHEMA (not _seed_cleansed) so the columns are genuinely
    # absent, not just NULL.
    schema = {
        c: t for c, t in _CLEANSED_SCHEMA.items()
        if c not in ("status", "txn_type", "operation_type")
    }
    row = {c: _CLEANSED_DEFAULTS.get(c) for c in schema if c != "id"}
    row["id"] = "t1"
    df = pl.DataFrame(
        {c: [row.get(c)] for c in schema}, schema=schema
    ).with_columns(pl.col("date").str.to_date("%Y-%m-%d"))
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)
    dim = pl.DataFrame({
        "account_uuid": ["u-bank", "u-card"],
        "pluggy_account_id": ["acc-bank", "acc-card"],
    })
    _delta_util.seed(tmp_path, "curated", "dim_account", dim)

    with pytest.raises(RuntimeError, match="v3 carried columns"):
        _run_c2c(tmp_path)
