import argparse
import glob as _glob
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

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
TEXT_COLS = ["id", "itemId", "name", "type", "subtype", "issuer", "institution"]
NUMERIC_COLS = ["balance", "amountOriginal"]  # A18: only fields with a known U7 consumer

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
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")

    # Project absent TEXT cols as null Utf8 and absent NUMERIC cols as null Float64.
    for col in TEXT_COLS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(col))
    if "currencyCode" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("currencyCode"))

    # Holdings are SCD Type 1 (keep latest, no history). Pluggy returns the full holding set
    # each fetch, so latest-per-id across all raw partitions is the current state — safe to
    # drop and rebuild every run. itemId is the institution FK (resolved to a name at U6).
    numeric_exprs = [
        (pl.col(c).cast(pl.Float64, strict=False) if c in df.columns
         else pl.lit(None, dtype=pl.Float64)).alias(c)
        for c in NUMERIC_COLS
    ]
    latest = (
        df.sort("ingested_at", descending=True)
        .unique(subset=["id"], keep="first", maintain_order=True)
        .select(
            *TEXT_COLS,
            pl.col("currencyCode").alias("currency"),
            *numeric_exprs,
            "ingested_at",
        )
    )

write_delta_overwrite("cleansed", "investments", latest)

print(f"investments: {len(latest)} rows written to cleansed")
