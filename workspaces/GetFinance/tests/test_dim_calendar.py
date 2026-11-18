import polars as pl

from tests import _delta_util

CALENDAR_COLS = {
    "date_id", "date", "year", "quarter", "month", "month_name", "day",
    "day_of_week", "day_name", "week_of_year", "is_weekend", "anomes_key", "Mes_Ano",
}


def _build(tmp_path, start: str, end: str) -> list[dict]:
    _delta_util.run_script("cleansed2curated", "dim_calendar",
                           ["--start", start, "--end", end], tmp_path)
    return _delta_util.read(tmp_path, "curated", "dim_calendar").sort("date_id").to_dicts()


def test_anomes_key_is_yyyymm_int(tmp_path):
    rows = _build(tmp_path, "2026-05-10", "2026-05-10")
    assert rows[0]["anomes_key"] == 202605


def test_mes_ano_is_abbreviated_pt_label(tmp_path):
    rows = _build(tmp_path, "2026-05-10", "2026-05-10")
    assert rows[0]["Mes_Ano"] == "mai/2026"


def test_parquet_has_exact_column_contract(tmp_path):
    rows = _build(tmp_path, "2026-05-10", "2026-05-10")
    assert set(rows[0].keys()) == CALENDAR_COLS


def test_date_column_is_date_type(tmp_path):
    # N6: dim_calendar.date must be a real DATE so fact star-joins (fact.snapshot_date = dim.date)
    # are clean DATE=DATE comparisons, not fragile VARCHAR<->DATE implicit casts.
    _delta_util.run_script("cleansed2curated", "dim_calendar",
                           ["--start", "2026-01-01", "--end", "2026-01-05"], tmp_path)
    df = _delta_util.read(tmp_path, "curated", "dim_calendar")
    assert df.schema["date"] == pl.Date, f"dim_calendar.date must be Date, got {df.schema['date']}"
