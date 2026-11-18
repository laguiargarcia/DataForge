import polars as pl
from pathlib import Path

from tests import _delta_util

ACC_RAW_COLS = ["id", "itemId", "type", "subtype", "name", "balance",
                "currencyCode", "number", "creditData", "bankData", "ingested_at"]


def _write_partition(raw_root: Path, records: list[dict], ingestion_date: str,
                     cols: list[str] = ACC_RAW_COLS) -> None:
    part_dir = raw_root / "accounts" / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / "data.parquet"
    data = {c: [(str(r.get(c)) if r.get(c) is not None else None) for r in records] for c in cols}
    pl.DataFrame(data, schema={c: pl.Utf8 for c in cols}).write_parquet(str(out))


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("raw2cleansed", "raw2cleansed_accounts",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)


def _read_accounts(tmp_path: Path) -> list[dict]:
    df = _delta_util.read(tmp_path, "cleansed", "accounts").sort("id")
    return df.to_dicts()


def test_accounts_latest_per_id_wins(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "acc-1", "itemId": "item-A", "type": "BANK", "subtype": "CHECKING",
         "name": "Checking", "balance": "100.0", "currencyCode": "BRL",
         "ingested_at": "2026-01-01T00:00:00Z"},
    ], "2026-01-01")
    _write_partition(raw, [
        {"id": "acc-1", "itemId": "item-A", "type": "BANK", "subtype": "CHECKING",
         "name": "Checking", "balance": "250.0", "currencyCode": "BRL",
         "ingested_at": "2026-01-02T00:00:00Z"},
    ], "2026-01-02")
    _run(tmp_path)
    rows = _read_accounts(tmp_path)
    assert len(rows) == 1, f"expected one deduped account, got {rows}"
    assert rows[0]["balance"] == 250.0, "latest ingested_at must win"


def test_balance_cast_to_double(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "acc-1", "itemId": "item-A", "type": "BANK", "subtype": "CHECKING",
         "name": "Checking", "balance": "1174.30", "currencyCode": "BRL",
         "ingested_at": "2026-01-01T00:00:00Z"},
    ], "2026-01-01")
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "accounts")
    assert df.schema["balance"] == pl.Float64
    assert df["balance"][0] == 1174.30


def test_carries_native_itemId(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "acc-1", "itemId": "item-XYZ", "type": "BANK", "subtype": "CHECKING",
         "name": "Checking", "balance": "10.0", "currencyCode": "BRL",
         "ingested_at": "2026-01-01T00:00:00Z"},
    ], "2026-01-01")
    _run(tmp_path)
    rows = _read_accounts(tmp_path)
    assert "itemId" in rows[0], "cleansed.accounts must carry the institution FK"
    assert rows[0]["itemId"] == "item-XYZ"


def test_currency_renamed_from_currencyCode(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "acc-1", "itemId": "item-A", "type": "BANK", "subtype": "CHECKING",
         "name": "Checking", "balance": "10.0", "currencyCode": "BRL",
         "ingested_at": "2026-01-01T00:00:00Z"},
    ], "2026-01-01")
    _run(tmp_path)
    rows = _read_accounts(tmp_path)
    assert "currency" in rows[0]
    assert "currencyCode" not in rows[0]
    assert rows[0]["currency"] == "BRL"


def test_both_account_types_present(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "acc_bank", "itemId": "item-A", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "name": "Conta", "balance": "500.0", "currencyCode": "BRL",
         "ingested_at": "2026-01-01T00:00:00Z"},
        {"id": "acc_card", "itemId": "item-A", "type": "CREDIT", "subtype": "CREDIT_CARD",
         "name": "Cartao", "balance": "1174.30", "currencyCode": "BRL",
         "creditData": "{'availableCreditLimit': 1725.70, 'creditLimit': 2900}",
         "ingested_at": "2026-01-01T00:00:00Z"},
    ], "2026-01-01")
    _run(tmp_path)
    rows = {r["id"]: r for r in _read_accounts(tmp_path)}
    assert rows["acc_bank"]["type"] == "BANK"
    assert rows["acc_card"]["type"] == "CREDIT"
    assert "availableCreditLimit" in rows["acc_card"]["creditData"]


def test_bank_only_item_without_creditData_column(tmp_path):
    # A bank-only item: raw partitions have NO creditData column at all (Pluggy omits it; the
    # raw writer only emits keys that appear). The script must still produce a row, with the
    # absent blob projected as NULL.
    raw = tmp_path / "raw"
    bank_only_cols = ["id", "itemId", "type", "subtype", "name", "balance",
                      "currencyCode", "number", "bankData", "ingested_at"]
    _write_partition(raw, [
        {"id": "acc-bank", "itemId": "item-A", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "name": "Conta", "balance": "500.0", "currencyCode": "BRL", "number": "12345123-6",
         "bankData": "{'transferNumber': '901/0001/12345123-6', 'closingBalance': 500.0}",
         "ingested_at": "2026-01-01T00:00:00Z"},
    ], "2026-01-01", cols=bank_only_cols)
    _run(tmp_path)
    rows = _read_accounts(tmp_path)
    assert len(rows) == 1
    assert rows[0]["id"] == "acc-bank"
    assert rows[0]["creditData"] is None


def test_empty_glob_creates_empty_typed_table(tmp_path):
    # No raw accounts partition at all → the glob matches nothing. Accounts are rebuilt every
    # run (SCD1 overwrite), so the script must still ship an empty, correctly-typed cleansed
    # table (mirrors raw2cleansed_investments) rather than crash on pl.concat([]).
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "accounts")
    assert df.height == 0
    assert df.columns == ["id", "itemId", "type", "subtype", "number", "transfer_number",
                          "name", "balance", "currency", "creditData", "ingested_at"]
    assert df.schema["balance"] == pl.Float64
    for c in ("id", "itemId", "type", "subtype", "number", "transfer_number",
              "name", "currency", "creditData", "ingested_at"):
        assert df.schema[c] == pl.Utf8


def test_carries_number_subtype_transfer_number(tmp_path):
    # D3: number, subtype, and transfer_number now flow to cleansed (the natural-key derivation
    # reads them). transfer_number is parsed out of the bankData repr blob.
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "a1", "itemId": "i", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "name": "Conta", "balance": "1.0", "currencyCode": "BRL", "number": "12345123-6",
         "bankData": "{'transferNumber': '901/0001/12345123-6', 'closingBalance': 1.0}",
         "ingested_at": "2026-01-01"},
    ], "2026-01-01")
    _run(tmp_path)
    r = _read_accounts(tmp_path)[0]
    assert r["subtype"] == "CHECKING_ACCOUNT"
    assert r["number"] == "12345123-6"
    assert r["transfer_number"] == "901/0001/12345123-6"


def test_transfer_number_none_for_credit(tmp_path):
    # CREDIT card has no bankData (None) → transfer_number parses to None, not a crash.
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "card", "itemId": "i", "type": "CREDIT", "subtype": "CREDIT_CARD",
         "name": "Cartao", "balance": "100.0", "currencyCode": "BRL", "number": "7781",
         "creditData": "{'level': 'GOLD'}", "ingested_at": "2026-01-01"},
    ], "2026-01-01")
    _run(tmp_path)
    r = _read_accounts(tmp_path)[0]
    assert r["transfer_number"] is None
    assert r["number"] == "7781"
    assert r["subtype"] == "CREDIT_CARD"


def test_malformed_bankData_transfer_number_none(tmp_path):
    # A malformed bankData blob must not crash; transfer_number degrades to None.
    raw = tmp_path / "raw"
    _write_partition(raw, [
        {"id": "a1", "itemId": "i", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
         "name": "Conta", "balance": "1.0", "currencyCode": "BRL", "number": "123",
         "bankData": "not-a-dict", "ingested_at": "2026-01-01"},
    ], "2026-01-01")
    _run(tmp_path)
    r = _read_accounts(tmp_path)[0]
    assert r["transfer_number"] is None
