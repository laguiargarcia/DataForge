import polars as pl
import pytest
from pathlib import Path

from tests import _delta_util

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _seed_cleansed_investments(tmp_path: Path, rows: list[dict]) -> None:
    """cleansed.investments Delta table in U5's output shape."""
    df = pl.DataFrame(
        {
            "id": [r.get("id") for r in rows],
            "itemId": [r.get("itemId", "item-ficticio") for r in rows],
            "name": [r.get("name", "CDB") for r in rows],
            "type": [r.get("type", "FIXED_INCOME") for r in rows],
            "subtype": [r.get("subtype", "CDB") for r in rows],
            "issuer": [r.get("issuer") for r in rows],
            "institution": [r.get("institution") for r in rows],
            "currency": [r.get("currency", "BRL") for r in rows],
            "balance": [r.get("balance") for r in rows],
            "amountOriginal": [r.get("amountOriginal") for r in rows],
            "amountProfit": [r.get("amountProfit") for r in rows],
            "ingested_at": [r.get("ingested_at", "2026-05-29T03:00:00Z") for r in rows],
        },
        schema={
            "id": pl.Utf8, "itemId": pl.Utf8, "name": pl.Utf8, "type": pl.Utf8,
            "subtype": pl.Utf8, "issuer": pl.Utf8, "institution": pl.Utf8,
            "currency": pl.Utf8, "balance": pl.Float64, "amountOriginal": pl.Float64,
            "amountProfit": pl.Float64, "ingested_at": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "cleansed", "investments", df)


def _seed_dim_investment(tmp_path: Path, mapping: dict) -> None:
    """mapping: pluggy_holding_id -> holding_uuid. dim_investment shape per U6."""
    df = pl.DataFrame(
        {
            "holding_uuid": list(mapping.values()),
            "pluggy_holding_id": list(mapping.keys()),
            "name": ["n" for _ in mapping],
            "type": ["FIXED_INCOME" for _ in mapping],
            "subtype": ["CDB" for _ in mapping],
            "issuer": ["ISS" for _ in mapping],
            "institution": ["Banco Fictício" for _ in mapping],
        },
        schema={
            "holding_uuid": pl.Utf8, "pluggy_holding_id": pl.Utf8, "name": pl.Utf8,
            "type": pl.Utf8, "subtype": pl.Utf8, "issuer": pl.Utf8, "institution": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "curated", "dim_investment", df)


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("cleansed2curated", "fact_investment_position",
                           ["--source", "cleansed", "--target", "curated"], tmp_path)


def _read_fact(tmp_path: Path) -> list[dict]:
    return (
        _delta_util.read(tmp_path, "curated", "fact_investment_position")
        .sort("holding_uuid", "snapshot_date")
        .to_dicts()
    )


def _fact_df(tmp_path: Path) -> pl.DataFrame:
    return _delta_util.read(tmp_path, "curated", "fact_investment_position")


def test_single_date_snapshot_values_and_types(tmp_path):
    # invested=amountOriginal, current=balance, currency carried; numerics typed DOUBLE/Float64.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 1906.25, "balance": 1987.40, "currency": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_investment(tmp_path, {"inv-cdb": "ficticio-cdb-2031-03"})
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["holding_uuid"] == "ficticio-cdb-2031-03"
    assert str(r["snapshot_date"]) == "2026-05-29"
    assert r["invested_amount"] == 1906.25, "invested_amount == cleansed.amountOriginal"
    assert r["current_value"] == 1987.40, "current_value == cleansed.balance"
    assert r["currency"] == "BRL"

    schema = dict(_fact_df(tmp_path).schema)
    assert schema["snapshot_date"] == pl.Date
    assert schema["invested_amount"] == pl.Float64
    assert schema["current_value"] == pl.Float64
    assert schema["return"] == pl.Float64
    assert schema["currency"] == pl.Utf8


def test_holding_uuid_resolved_from_dim(tmp_path):
    # cleansed.investments.id is the Pluggy id "inv-99"; dim maps it to slug "ficticio-treasury-2027".
    # The fact must carry the slug, never the raw Pluggy id (A1 — do not recompute the surrogate).
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-99", "amountOriginal": 500.0, "balance": 540.0,
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_investment(tmp_path, {"inv-99": "ficticio-treasury-2027"})
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    assert len(rows) == 1
    assert rows[0]["holding_uuid"] == "ficticio-treasury-2027", "fact carries dim slug, not Pluggy id"


def test_append_only_across_dates(tmp_path):
    # Day 1 capture
    _seed_dim_investment(tmp_path, {"inv-cdb": "ficticio-cdb-2031-03"})
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 2000.0, "balance": 2010.0,
         "ingested_at": "2026-05-28T03:00:00Z"},
    ])
    _run(tmp_path)

    # Day 2 capture — cleansed now holds the new day's reading (U5 re-ran, SCD-1 rebuild)
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 2000.0, "balance": 1987.40,
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    assert len(rows) == 2, "both days' positions must be preserved (append-only)"
    by_date = {str(r["snapshot_date"]): r["current_value"] for r in rows}
    assert by_date == {"2026-05-28": 2010.0, "2026-05-29": 1987.40}


def test_same_day_rerun_replaces_today_only(tmp_path):
    _seed_dim_investment(tmp_path, {"inv-cdb": "ficticio-cdb-2031-03"})
    # Day 1 — locked-in prior snapshot
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 2000.0, "balance": 2010.0,
         "ingested_at": "2026-05-28T03:00:00Z"},
    ])
    _run(tmp_path)
    # Day 2 — first run
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 2000.0, "balance": 1987.40,
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _run(tmp_path)
    # Day 2 — same-day re-fetch with a corrected value; must REPLACE today, not append
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 2000.0, "balance": 2150.00,
         "ingested_at": "2026-05-29T09:30:00Z"},
    ])
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    by_date = {str(r["snapshot_date"]): r["current_value"] for r in rows}
    assert len(rows) == 2, "same snapshot_date must upsert, not append"
    assert by_date["2026-05-29"] == 2150.00, "today's row replaced by the corrected re-fetch"
    assert by_date["2026-05-28"] == 2010.0, "prior snapshot_date must remain untouched (immutable)"


def test_return_is_current_minus_invested(tmp_path):
    # Fallback path (df-9uv): amountProfit is NULL on every row here (a bank that leaves it NULL), so return stays
    # the arithmetic current − invested. A negative-return holding proves the sign is honest
    # (current < invested), and a second holding proves it is a per-row subtraction (no
    # cross-holding netting).
    _seed_dim_investment(tmp_path, {"inv-up": "h-up", "inv-down": "h-down"})
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-up", "amountOriginal": 1000.0, "balance": 1234.50,
         "ingested_at": "2026-05-29T03:00:00Z"},
        {"id": "inv-down", "amountOriginal": 800.0, "balance": 742.25,
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _run(tmp_path)

    rows = {r["holding_uuid"]: r for r in _read_fact(tmp_path)}
    assert rows["h-up"]["return"] == pytest.approx(234.50), "gain: 1234.50 - 1000.0"
    assert rows["h-down"]["return"] == pytest.approx(-57.75), "loss: 742.25 - 800.0 (negative)"
    # per-row, not aggregated: each return equals THAT row's current - invested
    for r in rows.values():
        assert r["return"] == pytest.approx(r["current_value"] - r["invested_amount"])


def test_return_prefers_pluggy_amount_profit(tmp_path):
    # df-9uv: amountOriginal is gross-ever-deposited — never reduced by partial
    # withdrawals — so current − invested reads a withdrawal as a loss. When Pluggy supplies
    # amountProfit, return must be that P&L (2.98), NOT the arithmetic −543.03.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-modelo", "amountOriginal": 1080.0, "balance": 536.97, "amountProfit": 2.98,
         "ingested_at": "2026-06-20T03:00:00Z"},
    ])
    _seed_dim_investment(tmp_path, {"inv-modelo": "modelo-fund-2.98"})
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["return"] == pytest.approx(2.98), "return == Pluggy amountProfit, not current - invested"
    assert r["invested_amount"] == 1080.0, "invested_amount stays the faithful amountOriginal carry"
    assert r["current_value"] == 536.97


def test_unmapped_holding_excluded_from_fact(tmp_path):
    # Two holdings in cleansed; only one is in dim_investment. The unmapped (quarantined-at-U6)
    # holding has no stable identity, so it gets NO fact row — the inner JOIN drops it.
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-known", "amountOriginal": 1000.0, "balance": 1100.0,
         "ingested_at": "2026-05-29T03:00:00Z"},
        {"id": "inv-stranger", "amountOriginal": 5000.0, "balance": 5500.0,
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_investment(tmp_path, {"inv-known": "ficticio-known"})  # inv-stranger NOT mapped
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    assert [r["holding_uuid"] for r in rows] == ["ficticio-known"], "only mapped holdings get a fact row"
    assert all(r["current_value"] != 5500.0 for r in rows), "unmapped holding must not appear"


def test_empty_seam_produces_empty_typed_fact(tmp_path):
    _seed_cleansed_investments(tmp_path, [])      # U5 empty-seam output: table present, zero rows
    _seed_dim_investment(tmp_path, {"inv-x": "h-x"})  # dim present (need non-empty to read schema)
    _run(tmp_path)

    df = _fact_df(tmp_path)
    schema = dict(df.schema)
    assert len(df) == 0, "empty seam must yield zero rows, not crash"
    assert set(df.columns) == {"holding_uuid", "snapshot_date", "invested_amount",
                               "current_value", "return", "currency"}, "column contract (spec §5)"
    assert schema["snapshot_date"] == pl.Date
    assert schema["invested_amount"] == pl.Float64
    assert schema["current_value"] == pl.Float64
    assert schema["return"] == pl.Float64
    assert schema["currency"] == pl.Utf8


def test_matured_holding_leaves_gap_no_fabrication(tmp_path):
    _seed_dim_investment(tmp_path, {"inv-mat": "ficticio-cdb-matures", "inv-keep": "ficticio-cdb-keep"})

    # Day 1: both holdings present
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-mat", "amountOriginal": 1000.0, "balance": 1005.0,
         "ingested_at": "2026-05-28T03:00:00Z"},
        {"id": "inv-keep", "amountOriginal": 2000.0, "balance": 2010.0,
         "ingested_at": "2026-05-28T03:00:00Z"},
    ])
    _run(tmp_path)

    # Day 2: inv-mat has matured/sold — Pluggy no longer returns it, so U5's SCD-1 rebuild of
    # cleansed lists only inv-keep. (Model the rebuild by reseeding cleansed with just inv-keep.)
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-keep", "amountOriginal": 2000.0, "balance": 2125.0,
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _run(tmp_path)

    rows = _read_fact(tmp_path)
    keyed = {(r["holding_uuid"], str(r["snapshot_date"])): r for r in rows}

    # Day-1 row for the matured holding is preserved (append-only never drops prior dates)
    assert ("ficticio-cdb-matures", "2026-05-28") in keyed
    assert keyed[("ficticio-cdb-matures", "2026-05-28")]["current_value"] == 1005.0
    # NO day-2 row for the matured holding — honest gap, not a carried-forward or zero-filled value
    assert ("ficticio-cdb-matures", "2026-05-29") not in keyed, \
        "matured holding must leave a gap on day 2 (no LOCF, no fabrication — A11)"
    # The surviving holding accumulates both days
    assert keyed[("ficticio-cdb-keep", "2026-05-28")]["current_value"] == 2010.0
    assert keyed[("ficticio-cdb-keep", "2026-05-29")]["current_value"] == 2125.0
    # Total rows: 2 (day1) + 1 (day2) = 3, NOT 4 — the gap is real
    assert len(rows) == 3


def test_return_matches_expected(tmp_path):
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb", "amountOriginal": 1906.25, "balance": 1987.40, "currency": "BRL",
         "ingested_at": "2026-05-29T03:00:00Z"},
    ])
    _seed_dim_investment(tmp_path, {"inv-cdb": "ficticio-cdb-2031-03"})
    _run(tmp_path)

    rows = _fact_df(tmp_path).select(
        "holding_uuid", "invested_amount", "current_value", "return", "currency"
    ).sort("holding_uuid").rows()
    assert rows[0][0] == "ficticio-cdb-2031-03"
    assert rows[0][1] == 1906.25
    assert rows[0][2] == 1987.40
    assert rows[0][3] == pytest.approx(81.15)
    assert rows[0][4] == "BRL"


def test_no_duckdb_import():
    # Delta port: the script must not import duckdb (the package is uninstalled in this env).
    src = (SCRIPTS / "cleansed2curated" / "fact_investment_position.py").read_text()
    assert "import duckdb" not in src, "fact_investment_position must not import duckdb (Delta port)"
