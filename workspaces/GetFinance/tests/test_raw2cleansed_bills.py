import polars as pl
from pathlib import Path

from tests import _delta_util

# Real raw bills partitions are all-VARCHAR; payments/financeCharges are repr-string blobs (the
# parquet writer stringifies every value). dueDate/updatedAt are full ISO datetimes.
BILL_RAW_COLS = ["id", "accountId", "accountName", "dueDate", "totalAmount",
                 "totalAmountCurrencyCode", "minimumPaymentAmount", "financeCharges",
                 "payments", "allowsInstallments", "createdAt", "updatedAt", "ingested_at"]

FULL_PAYMENT = ("[{'valueType': 'FULL_PAYMENT', 'paymentDate': '2026-05-18T00:00:00.000Z', "
                "'amount': 318.45, 'paymentMode': 'DEBIT_ACCOUNT', 'currencyCode': 'BRL'}]")


def _write_partition(raw_root: Path, records: list[dict], ingestion_date: str,
                     cols: list[str] = BILL_RAW_COLS) -> None:
    part_dir = raw_root / "bills" / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / "data.parquet"
    data = {c: [(str(r.get(c)) if r.get(c) is not None else None) for r in records] for c in cols}
    pl.DataFrame(data, schema={c: pl.Utf8 for c in cols}).write_parquet(str(out))


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("raw2cleansed", "raw2cleansed_bills",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)


def _read_bills(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "cleansed", "bills").sort("id").to_dicts()


def test_typed_cleansed_schema(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "bill-1", "accountId": "acc-card", "accountName": "Cartao",
         "dueDate": "2026-05-17T00:00:00.000Z", "totalAmount": "318.45",
         "totalAmountCurrencyCode": "BRL", "minimumPaymentAmount": "31.85",
         "financeCharges": "[]", "payments": FULL_PAYMENT, "allowsInstallments": "True",
         "updatedAt": "2026-05-19T00:00:00.000Z", "ingested_at": "2026-06-04"},
    ], "2026-06-04")
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "bills")
    assert df.columns == ["id", "accountId", "dueDate", "totalAmount",
                          "totalAmountCurrencyCode", "minimumPaymentAmount", "financeCharges",
                          "payments", "allowsInstallments", "updatedAt", "ingested_at"]
    assert df.schema["totalAmount"] == pl.Float64
    assert df.schema["minimumPaymentAmount"] == pl.Float64
    assert df.schema["allowsInstallments"] == pl.Boolean
    r = df.to_dicts()[0]
    assert r["totalAmount"] == 318.45
    assert r["minimumPaymentAmount"] == 31.85
    assert r["allowsInstallments"] is True
    # payments/financeCharges kept as repr-string blobs (parsed later in curated)
    assert r["payments"] == FULL_PAYMENT
    assert r["financeCharges"] == "[]"


def test_latest_per_id_dedup_across_days(tmp_path):
    # Same bill id captured on two days (e.g. an unpaid bill that later gets paid). Latest
    # ingested_at wins (SCD-1) — the paid state should survive.
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "bill-1", "accountId": "acc-card", "dueDate": "2026-05-17T00:00:00.000Z",
         "totalAmount": "318.45", "totalAmountCurrencyCode": "BRL",
         "minimumPaymentAmount": "31.85", "financeCharges": "[]", "payments": "[]",
         "allowsInstallments": "True", "updatedAt": "2026-05-17T00:00:00.000Z",
         "ingested_at": "2026-05-17"},
    ], "2026-05-17")
    _write_partition(raw, [
        {"id": "bill-1", "accountId": "acc-card", "dueDate": "2026-05-17T00:00:00.000Z",
         "totalAmount": "318.45", "totalAmountCurrencyCode": "BRL",
         "minimumPaymentAmount": "31.85", "financeCharges": "[]", "payments": FULL_PAYMENT,
         "allowsInstallments": "True", "updatedAt": "2026-05-19T00:00:00.000Z",
         "ingested_at": "2026-05-19"},
    ], "2026-05-19")
    _run(tmp_path)
    rows = _read_bills(tmp_path)
    assert len(rows) == 1, f"expected one deduped bill, got {rows}"
    assert rows[0]["payments"] == FULL_PAYMENT, "latest ingested_at (paid state) must win"
    assert rows[0]["ingested_at"] == "2026-05-19"


def test_distinct_ids_across_days_all_kept(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "bill-apr", "accountId": "acc-card", "dueDate": "2026-04-17T00:00:00.000Z",
         "totalAmount": "200.0", "totalAmountCurrencyCode": "BRL",
         "minimumPaymentAmount": "20.0", "financeCharges": "[]", "payments": "[]",
         "allowsInstallments": "True", "updatedAt": "2026-04-17T00:00:00.000Z",
         "ingested_at": "2026-05-01"},
    ], "2026-05-01")
    _write_partition(raw, [
        {"id": "bill-may", "accountId": "acc-card", "dueDate": "2026-05-17T00:00:00.000Z",
         "totalAmount": "318.45", "totalAmountCurrencyCode": "BRL",
         "minimumPaymentAmount": "31.85", "financeCharges": "[]", "payments": FULL_PAYMENT,
         "allowsInstallments": "True", "updatedAt": "2026-05-19T00:00:00.000Z",
         "ingested_at": "2026-06-01"},
    ], "2026-06-01")
    _run(tmp_path)
    rows = _read_bills(tmp_path)
    assert {r["id"] for r in rows} == {"bill-apr", "bill-may"}
    assert len(rows) == 2


def test_empty_glob_creates_empty_typed_table(tmp_path):
    # No raw bills partition at all → empty, correctly-typed cleansed table (mirrors accounts).
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "bills")
    assert df.height == 0
    assert df.columns == ["id", "accountId", "dueDate", "totalAmount",
                          "totalAmountCurrencyCode", "minimumPaymentAmount", "financeCharges",
                          "payments", "allowsInstallments", "updatedAt", "ingested_at"]
    assert df.schema["totalAmount"] == pl.Float64
    assert df.schema["allowsInstallments"] == pl.Boolean


def test_zero_total_bill_carried(tmp_path):
    # A Sep-2025 zero-total bill with no payments must survive cleansing typed.
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "bill-zero", "accountId": "acc-card", "dueDate": "2025-09-17T00:00:00.000Z",
         "totalAmount": "0", "totalAmountCurrencyCode": "BRL", "minimumPaymentAmount": "0",
         "financeCharges": "[]", "payments": "[]", "allowsInstallments": "False",
         "updatedAt": "2025-09-18T00:00:00.000Z", "ingested_at": "2026-06-04"},
    ], "2026-06-04")
    _run(tmp_path)
    r = _read_bills(tmp_path)[0]
    assert r["totalAmount"] == 0.0
    assert r["allowsInstallments"] is False
    assert r["payments"] == "[]"
