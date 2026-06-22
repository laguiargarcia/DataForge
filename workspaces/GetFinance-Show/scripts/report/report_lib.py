"""report_lib.py — Pure Polars helpers for GetFinance report v2 (df-dgu).

Task 1 (df-u6r): liquidity_per_month — semi-additive liquidity aggregate per month.
"""

import polars as pl

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

_OUTPUT_SCHEMA = {
    "anomes_key": pl.Int32,
    "mes_ano": pl.String,
    "cash_disponivel": pl.Float64,
    "card_outstanding": pl.Float64,
    "saldo_disponivel": pl.Float64,
    "saidas_conta": pl.Float64,
}


def _empty_result() -> pl.DataFrame:
    return pl.DataFrame({col: pl.Series([], dtype=dtype) for col, dtype in _OUTPUT_SCHEMA.items()})


def liquidity_per_month(snap: pl.DataFrame, fact: pl.DataFrame) -> pl.DataFrame:
    """Return one row per snapshot month with semi-additive liquidity figures.

    Args:
        snap: fact_balance_snapshot rows. Required columns:
              account_uuid (Utf8), snapshot_date (Date), account_type (Utf8),
              available (Float64, nullable), card_outstanding (Float64, nullable).
        fact: fact_transaction rows. Required columns:
              date (Date), account_type (Utf8), amount_signed (Float64).

    Returns:
        DataFrame with columns: anomes_key, mes_ano, cash_disponivel,
        card_outstanding, saldo_disponivel, saidas_conta.
        One row per snapshot month, sorted by anomes_key ascending.
    """
    if snap.is_empty():
        return _empty_result()

    # --- Step 1: derive snapshot month key ---
    snap = snap.with_columns(
        (
            pl.col("snapshot_date").dt.year() * 100 + pl.col("snapshot_date").dt.month()
        ).cast(pl.Int32).alias("anomes_key")
    )

    # --- Step 2: latest snapshot per (anomes_key, account_uuid) ---
    latest_dates = (
        snap
        .group_by(["anomes_key", "account_uuid"])
        .agg(pl.col("snapshot_date").max().alias("latest_date"))
    )
    snap_latest = (
        snap
        .join(latest_dates, on=["anomes_key", "account_uuid"], how="inner")
        .filter(pl.col("snapshot_date") == pl.col("latest_date"))
        .drop("latest_date")
    )

    # --- Step 3: aggregate cash and card per month ---
    bank = (
        snap_latest
        .filter(pl.col("account_type") == "BANK")
        .group_by("anomes_key")
        .agg(
            pl.col("available").fill_null(0.0).sum().alias("cash_disponivel")
        )
    )
    card = (
        snap_latest
        .filter(pl.col("account_type") == "CREDIT")
        .group_by("anomes_key")
        .agg(
            pl.col("card_outstanding").fill_null(0.0).sum().alias("card_outstanding")
        )
    )

    # All snapshot months (union of BANK + CREDIT months, driven by snap)
    all_months = snap_latest.select("anomes_key").unique()

    liquidity = (
        all_months
        .join(bank, on="anomes_key", how="left")
        .join(card, on="anomes_key", how="left")
        .with_columns([
            pl.col("cash_disponivel").fill_null(0.0),
            pl.col("card_outstanding").fill_null(0.0),
        ])
        .with_columns(
            (pl.col("cash_disponivel") - pl.col("card_outstanding")).alias("saldo_disponivel")
        )
    )

    # --- Step 4: saidas_conta — BANK outflows per month from fact ---
    outflows = (
        fact
        .filter(
            (pl.col("account_type") == "BANK") & (pl.col("amount_signed") < 0)
        )
        .with_columns(
            (
                pl.col("date").dt.year() * 100 + pl.col("date").dt.month()
            ).cast(pl.Int32).alias("anomes_key")
        )
        .group_by("anomes_key")
        .agg(
            (-pl.col("amount_signed").sum()).alias("saidas_conta")
        )
    )

    # --- Step 5: left-join outflows onto snapshot months ---
    result = (
        liquidity
        .join(outflows, on="anomes_key", how="left")
        .with_columns(pl.col("saidas_conta").fill_null(0.0))
    )

    # --- Step 6: derive mes_ano label ---
    result = result.with_columns(
        pl.col("anomes_key").map_elements(
            lambda k: f"{MESES[(k % 100) - 1]}/{k // 100}",
            return_dtype=pl.String,
        ).alias("mes_ano")
    )

    # --- Step 7: final column order, sort ---
    return (
        result
        .select(["anomes_key", "mes_ano", "cash_disponivel", "card_outstanding",
                 "saldo_disponivel", "saidas_conta"])
        .sort("anomes_key")
    )
