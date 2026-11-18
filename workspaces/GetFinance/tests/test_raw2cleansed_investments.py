import polars as pl
from pathlib import Path

from tests import _delta_util

# Representative raw investments columns (subset of the live Pluggy payload that U5 carries
# forward — identity, descriptive, and the numerics U6/U7 will consume).
INV_RAW_COLS = ["id", "itemId", "name", "type", "subtype", "issuer", "institution",
                "currencyCode", "balance", "amount", "amountOriginal", "amountProfit",
                "amountWithdrawal", "taxes", "value", "quantity", "ingested_at"]


def _write_partition(raw_root: Path, records: list[dict], ingestion_date: str,
                     cols: list[str] = INV_RAW_COLS) -> None:
    part_dir = raw_root / "investments" / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / "data.parquet"
    data = {c: [(str(r.get(c)) if r.get(c) is not None else None) for r in records] for c in cols}
    pl.DataFrame(data, schema={c: pl.Utf8 for c in cols}).write_parquet(str(out))


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("raw2cleansed", "raw2cleansed_investments",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)


def _read_investments(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "cleansed", "investments").sort("id").to_dicts()


def _holding(**overrides) -> dict:
    base = {
        "id": "inv-1", "itemId": "item-A", "name": "CDB - BANCO EXEMPLO", "type": "FIXED_INCOME",
        "subtype": "CDB", "issuer": "BANCO FICTICIO S.A.", "institution": None,
        "currencyCode": "BRL", "balance": "1987.40", "amount": "2013.55",
        "amountOriginal": "1906.25", "amountProfit": None, "amountWithdrawal": "1987.40",
        "taxes": "26.15", "value": "0.01056288", "quantity": "190625",
        "ingested_at": "2026-01-01",  # A16: day-granular stamp
    }
    base.update(overrides)
    return base


def test_investments_latest_per_holding_wins(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [_holding(balance="2000.0", ingested_at="2026-01-01")],
                     "2026-01-01")
    _write_partition(raw, [_holding(balance="1987.40", ingested_at="2026-01-02")],
                     "2026-01-02")
    _run(tmp_path)
    rows = _read_investments(tmp_path)
    assert len(rows) == 1, f"expected one deduped holding, got {rows}"
    assert rows[0]["balance"] == 1987.40, "latest ingested_at must win"


def test_numeric_fields_cast_to_double(tmp_path):
    # The fact-position numerics (U7 invested_amount/current_value/return) must be typed in
    # cleansed, not left as VARCHAR strings from the raw layer.
    raw = tmp_path / "raw"
    _write_partition(raw, [_holding(),
                           _holding(id="inv-2", amountProfit="2.98")], "2026-01-01")
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "investments")
    for col in ("balance", "amountOriginal", "amountProfit"):
        assert df.schema[col] == pl.Float64, f"{col} should be Float64, got {df.schema[col]}"
    rows = _read_investments(tmp_path)
    assert rows[0]["amountOriginal"] == 1906.25
    assert rows[0]["amountProfit"] is None    # Pluggy leaves it NULL for some banks
    assert rows[1]["amountProfit"] == 2.98    # Pluggy's own P&L when the bank sends it, cast to Float64 (df-9uv)


def test_speculative_numerics_dropped(tmp_path):
    # A18: cleansed.investments carries balance + amountOriginal + amountProfit (df-9uv: U7
    # consumes Pluggy's own P&L) as numerics. The rest had no consumer (amountWithdrawal ==
    # balance dup, amount derivable); U6/U7 re-add precisely what their locked mapping needs.
    raw = tmp_path / "raw"
    _write_partition(raw, [_holding()], "2026-01-01")
    _run(tmp_path)
    cols = set(_read_investments(tmp_path)[0].keys())
    for dropped in ("amount", "amountWithdrawal", "taxes", "value", "quantity"):
        assert dropped not in cols, f"{dropped} should be dropped (no consumer; A18)"
    assert {"balance", "amountOriginal", "amountProfit"} <= cols


def test_carries_native_itemId(tmp_path):
    # itemId is the institution FK, resolved to a name at U6 (mirrors accounts→U3 via A10/A1).
    raw = tmp_path / "raw"
    _write_partition(raw, [_holding(itemId="item-XYZ")], "2026-01-01")
    _run(tmp_path)
    rows = _read_investments(tmp_path)
    assert "itemId" in rows[0], "cleansed.investments must carry the institution FK"
    assert rows[0]["itemId"] == "item-XYZ"


def test_currency_renamed_from_currencyCode(tmp_path):
    raw = tmp_path / "raw"
    _write_partition(raw, [_holding(currencyCode="BRL")], "2026-01-01")
    _run(tmp_path)
    rows = _read_investments(tmp_path)
    assert "currency" in rows[0]
    assert "currencyCode" not in rows[0]
    assert rows[0]["currency"] == "BRL"


def test_carries_dim_and_fact_fields(tmp_path):
    # Everything U6 (dim_investment) and U7 (fact_investment_position) read must survive U5.
    raw = tmp_path / "raw"
    _write_partition(raw, [_holding(
        name="CDB - BANCO EXEMPLO", type="FIXED_INCOME", subtype="CDB", issuer="BANCO FICTICIO S.A.",
    )], "2026-01-01")
    _run(tmp_path)
    row = _read_investments(tmp_path)[0]
    # dim_investment attributes
    assert row["name"] == "CDB - BANCO EXEMPLO"
    assert row["type"] == "FIXED_INCOME"
    assert row["subtype"] == "CDB"
    assert row["issuer"] == "BANCO FICTICIO S.A."
    assert "institution" in row
    # fact_investment_position numerics (invested vs current; mapping locked at U7)
    assert row["amountOriginal"] == 1906.25
    assert row["balance"] == 1987.40


def test_no_investments_produces_empty_table(tmp_path):
    # Spec §8 clean seam: a Pluggy item exposing no investments writes no raw partition
    # (landing2raw guards with `if all_investments`). U5 must still produce an empty,
    # correctly-typed cleansed.investments table rather than crash on a zero-match glob.
    (tmp_path / "raw").mkdir()
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "investments")
    assert df.height == 0, "empty seam must yield zero rows, not crash"
    assert df.schema["balance"] == pl.Float64, "schema contract preserved even when empty"
    assert df.schema["itemId"] == pl.Utf8
    assert "currency" in df.columns
    assert "currencyCode" not in df.columns


def test_missing_optional_column_projected_null(tmp_path):
    # A holding class whose raw partitions lack an optional column entirely (e.g. an EQUITY
    # item with no issuer/amountOriginal). diagonal_relaxed cannot invent the column, so the
    # script must project it as typed NULL rather than crash (mirrors the bank-only accounts
    # case in test_raw2cleansed_accounts.py).
    raw = tmp_path / "raw"
    minimal_cols = ["id", "itemId", "name", "type", "subtype", "currencyCode",
                    "balance", "ingested_at"]
    _write_partition(raw, [{
        "id": "inv-eq", "itemId": "item-A", "name": "FICT3", "type": "EQUITY",
        "subtype": "STOCK", "currencyCode": "BRL", "balance": "999.5",
        "ingested_at": "2026-01-01",
    }], "2026-01-01", cols=minimal_cols)
    _run(tmp_path)
    rows = _read_investments(tmp_path)
    assert len(rows) == 1
    assert rows[0]["id"] == "inv-eq"
    assert rows[0]["issuer"] is None
    assert rows[0]["amountOriginal"] is None
    assert rows[0]["amountProfit"] is None
    assert rows[0]["balance"] == 999.5
