import argparse
import glob as _glob
import polars as pl
from pathlib import Path

exec((Path(__file__).parent.parent / "functions" / "functions.py").read_text(encoding="utf-8"))

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--target", required=True)
args = parser.parse_args()

src_root = _connection_root(args.source)

inv_glob = str(src_root / "investments" / "ingestion_date=*" / "data.parquet")

# Carried forward to cleansed: identity + descriptive (Utf8), the numerics U6/U7 consume
# (Float64), and currencyCode renamed to currency. Pluggy returns a different key set per
# holding class (FIXED_INCOME vs EQUITY vs MUTUAL_FUND), and the raw writer only emits keys
# that appear in a fetch — so any column may be absent from every partition. Project absent
# columns defensively as typed NULL (mirrors the credit/bankData pattern in
# raw2cleansed_accounts.py), since diagonal_relaxed cannot invent a column that never appears.
# dueDate (df-dsy4): a matured fixed-income holding leaves the position only once its redemption reaches
# the account; the report needs the maturity day for that.
TEXT_COLS = ["id", "itemId", "name", "type", "subtype", "issuer", "institution", "dueDate"]
# A18: only fields with a known U7 consumer. amountProfit joined the list in df-9uv: some banks
# populate it (Pluggy's own P&L) while amountOriginal is gross-ever-deposited — never reduced
# by partial withdrawals — so balance − amountOriginal conflates withdrawals with losses.
NUMERIC_COLS = ["balance", "amountOriginal", "amountProfit"]

# Fixed cleansed schema (column order: TEXT_COLS, currency, NUMERIC_COLS, ingested_at).
EMPTY_SCHEMA = {
    **{c: pl.Utf8 for c in TEXT_COLS},
    "currency": pl.Utf8,
    **{c: pl.Float64 for c in NUMERIC_COLS},
    "ingested_at": pl.Utf8,
}

files = sorted(_glob.glob(inv_glob))

if not files:
    # Spec §8 clean seam: a Pluggy item exposing no investments writes no raw partition
    # (landing2raw guards with `if all_investments`), so the glob matches nothing. Ship an
    # empty, correctly-typed table instead — the column contract is known in-script, so
    # downstream U6/U7 see the same schema either way.
    latest = pl.DataFrame(schema=EMPTY_SCHEMA)
else:
    # Holdings are SCD Type 1 (keep latest, no history). Pluggy returns the full holding set
    # each fetch, so latest-per-id across all raw partitions is the current state — safe to
    # drop and rebuild every run. itemId is the institution FK (resolved to a name at U6).
    raw = read_raw(files)
    validate(raw, "raw.investments", RAW_INVESTMENTS_RULES)
    df = latest_by_key([raw], ["id"], "ingested_at")

    # Project absent TEXT cols as null Utf8 and absent NUMERIC cols as null Float64.
    for col in TEXT_COLS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(col))
    if "currencyCode" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("currencyCode"))

    numeric_exprs = [
        (pl.col(c).cast(pl.Float64, strict=False) if c in df.columns
         else pl.lit(None, dtype=pl.Float64)).alias(c)
        for c in NUMERIC_COLS
    ]
    latest = df.select(
        *TEXT_COLS,
        pl.col("currencyCode").alias("currency"),
        *numeric_exprs,
        "ingested_at",
    )

write_delta_overwrite("cleansed", "investments", latest)

# Reconciliation (df-ahua.22): every raw id is in cleansed once.
validate(layer_totals(latest, read_delta_table("cleansed", "investments"), ["id"]),
         "cleansed.investments vs raw", IDS_KEPT_RULES)

print(f"investments: {len(latest)} rows written to cleansed")
