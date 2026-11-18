import polars as pl
import pytest
from pathlib import Path

from tests import _delta_util

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _seed_raw_accounts(tmp_path: Path, ingestion_date: str, rows: list[dict]) -> None:
    # Real raw partitions are all-VARCHAR and live at raw/accounts/ingestion_date=<d>/data.parquet
    # (one parquet per fetch day, the permanent log fact_balance_snapshot rebuilds from).
    # currencyCode (not currency) and a creditData repr-string mirror the live Pluggy payload.
    keys, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen:
                keys.append(k); seen.add(k)
    data = {k: [(str(r[k]) if r.get(k) is not None else None) for r in rows] for k in keys}
    df = pl.DataFrame(data, schema={k: pl.Utf8 for k in keys})
    part_dir = tmp_path / "raw" / "accounts" / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(str(part_dir / "data.parquet"))


def _seed_dim_account(tmp_path: Path, mapping: dict) -> None:
    df = pl.DataFrame(
        {
            "account_uuid": list(mapping.values()),
            "pluggy_account_id": list(mapping.keys()),
            "name": ["n" for _ in mapping],
            "institution": ["Banco Fictício" for _ in mapping],
            "type": ["BANK" for _ in mapping],
            "currency": ["BRL" for _ in mapping],
        },
        schema={
            "account_uuid": pl.Utf8, "pluggy_account_id": pl.Utf8, "name": pl.Utf8,
            "institution": pl.Utf8, "type": pl.Utf8, "currency": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "curated", "dim_account", df)


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("cleansed2curated", "fact_balance_snapshot",
                           ["--source", "raw", "--target", "curated"], tmp_path)


def _read_fact(tmp_path: Path) -> list[dict]:
    return (
        _delta_util.read(tmp_path, "curated", "fact_balance_snapshot")
        .sort("account_uuid", "snapshot_date")
        .to_dicts()
    )


# A realistic CREDIT creditData repr-string (Python repr, as raw stores it).
CREDIT_DATA = ("{'level': 'GOLD', 'brand': 'VISA', 'balanceCloseDate': None, "
               "'balanceDueDate': '2026-05-17', 'availableCreditLimit': 1725.70, "
               "'minimumPayment': 31.85, 'creditLimit': 2900, 'status': 'ACTIVE'}")


def test_one_snapshot_row_per_account(tmp_path):
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "2241.60", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-bank": "acme-checking"})
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["account_uuid"] == "acme-checking"
    assert str(r["snapshot_date"]) == "2026-05-29"
    assert r["account_type"] == "BANK"
    assert r["balance"] == 2241.60
    assert r["currency"] == "BRL"


def test_credit_card_outstanding_and_rich_fields(tmp_path):
    # CREDIT: available is NULL (cash is BANK-only); the rich card fields carry the parsed
    # creditData (availableCreditLimit, creditLimit, minimumPayment, balanceDueDate).
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-credit", "type": "CREDIT", "balance": "1174.30", "currencyCode": "BRL",
         "creditData": CREDIT_DATA, "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-credit": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["card_outstanding"] == 1174.30
    assert r["available"] is None
    assert r["available_credit_limit"] == 1725.70
    assert r["credit_limit"] == 2900.0
    assert r["minimum_payment"] == 31.85
    assert str(r["statement_due_date"]) == "2026-05-17"
    assert r["statement_close_date"] is None


def test_bank_card_fields_null_available_is_balance(tmp_path):
    # BANK: available == balance; all card fields NULL.
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "2241.60", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-bank": "acme-checking"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["card_outstanding"] is None
    assert r["available"] == 2241.60
    assert r["available_credit_limit"] is None
    assert r["credit_limit"] is None
    assert r["minimum_payment"] is None
    assert r["statement_due_date"] is None


def test_rebuild_n_raw_days_to_n_snapshot_rows(tmp_path):
    # Rebuild-from-raw: N raw ingestion_date partitions for an account → N snapshot rows
    # (one per captured day). Replaces the old append-only-across-dates test.
    _seed_raw_accounts(tmp_path, "2026-05-28", [
        {"id": "p-bank", "type": "BANK", "balance": "100.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-28T03:00:00Z"},
    ])
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "250.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-bank": "acme-checking"})
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    assert len(rows) == 2, "each raw day must yield its own snapshot row"
    by_date = {str(r["snapshot_date"]): r["balance"] for r in rows}
    assert by_date == {"2026-05-28": 100.0, "2026-05-29": 250.0}


def test_overwrite_replaces_prior_rebuild(tmp_path):
    # write_delta_overwrite is DROP+CREATE: a second run reflects the current raw partition set
    # exactly. Removing the older raw day means the rebuild no longer carries it (raw is the
    # system of record — no separate "preserve prior rows" merge step).
    _seed_dim_account(tmp_path, {"p-bank": "acme-checking"})
    _seed_raw_accounts(tmp_path, "2026-05-28", [
        {"id": "p-bank", "type": "BANK", "balance": "100.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-28T03:00:00Z"},
    ])
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "250.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _run(tmp_path)
    assert len(_read_fact(tmp_path)) == 2

    # Drop the older raw partition, then re-run: overwrite rebuilds only from present partitions.
    import shutil
    shutil.rmtree(tmp_path / "raw" / "accounts" / "ingestion_date=2026-05-28")
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    assert len(rows) == 1, "overwrite rebuilds from present raw partitions only"
    assert str(rows[0]["snapshot_date"]) == "2026-05-29"
    assert rows[0]["balance"] == 250.0


def test_same_day_rerun_idempotent(tmp_path):
    # Re-running against the same raw partition set is idempotent (overwrite rebuilds identically).
    _seed_dim_account(tmp_path, {"p-bank": "acme-checking"})
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "175.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _run(tmp_path)
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    assert len(rows) == 1
    assert rows[0]["balance"] == 175.0


def test_credit_creditdata_none_card_fields_none(tmp_path):
    # CREDIT account with no creditData payload: outstanding = balance, available NULL,
    # rich card fields all NULL (no payload to parse).
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-credit", "type": "CREDIT", "balance": "1174.30", "currencyCode": "BRL",
         "creditData": None, "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-credit": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["card_outstanding"] == 1174.30
    assert r["available"] is None
    assert r["available_credit_limit"] is None
    assert r["credit_limit"] is None
    assert r["minimum_payment"] is None


def test_credit_creditdata_malformed_card_fields_none(tmp_path):
    # Malformed creditData string must not crash the run; card fields degrade to None.
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-credit", "type": "CREDIT", "balance": "500.0", "currencyCode": "BRL",
         "creditData": "not-a-dict", "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-credit": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["card_outstanding"] == 500.0
    assert r["available"] is None
    assert r["available_credit_limit"] is None


def test_raw_account_missing_from_dim_account_is_refused(tmp_path):
    # dim_account has every account (D3), so a raw account the inner join would drop is a lost
    # account: the reconciliation refuses the write (df-ahua.4.11).
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "250.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"some-other-pluggy-id": "acme-checking"})
    with pytest.raises(Exception, match="curated.fact_balance_snapshot vs raw: rows not carried") as exc:
        _run(tmp_path)
    assert type(exc.value).__name__ == "DataValidationError"
    assert not (tmp_path / "curated" / "fact_balance_snapshot").exists()


def test_diagonal_relaxed_unions_partitions_missing_creditdata(tmp_path):
    # A bank-only fetch day omits creditData entirely; a later day includes it. diagonal_relaxed
    # unions the differing column sets so the rebuild covers both days.
    _seed_raw_accounts(tmp_path, "2026-05-28", [
        {"id": "p-bank", "type": "BANK", "balance": "100.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-28T03:00:00Z"},
    ])
    _seed_raw_accounts(tmp_path, "2026-05-29", [
        {"id": "p-bank", "type": "BANK", "balance": "120.0", "currencyCode": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
        {"id": "p-credit", "type": "CREDIT", "balance": "1174.30", "currencyCode": "BRL",
         "creditData": CREDIT_DATA, "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_account(tmp_path, {"p-bank": "acme-checking", "p-credit": "acme-credit"})
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    assert len(rows) == 3
    credit = next(r for r in rows if r["account_uuid"] == "acme-credit")
    assert credit["available_credit_limit"] == 1725.70


def test_no_duckdb_import():
    # Delta port: the script must not import duckdb (the package is uninstalled in this env).
    src = (SCRIPTS / "cleansed2curated" / "fact_balance_snapshot.py").read_text()
    assert "import duckdb" not in src, "fact_balance_snapshot must not import duckdb (Delta port)"
