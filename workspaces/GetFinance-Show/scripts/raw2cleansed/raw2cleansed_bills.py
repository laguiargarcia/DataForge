import argparse
import glob as _glob
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # raw (all bills ingestion_date partitions)
parser.add_argument("--target", required=True)   # cleansed
args = parser.parse_args()

src_root = _connection_root(args.source)
bills_glob = str(src_root / "bills" / "ingestion_date=*" / "data.parquet")

# Raw partitions are all-VARCHAR and each fetch only emits the keys present in that fetch, so
# column sets differ per file. diagonal_relaxed unions columns and fills missing as null
# (mirrors raw2cleansed_accounts.py). Fixed cleansed output schema: identity/descriptive Utf8,
# numeric Float64, bool as Boolean. payments / financeCharges are kept as their raw repr-string
# blobs (Utf8) — parsed in curated (fact_card_statement.py), mirroring how creditData flows.
OUTPUT_SCHEMA = {
    "id": pl.Utf8,
    "accountId": pl.Utf8,
    "dueDate": pl.Utf8,
    "totalAmount": pl.Float64,
    "totalAmountCurrencyCode": pl.Utf8,
    "minimumPaymentAmount": pl.Float64,
    "financeCharges": pl.Utf8,
    "payments": pl.Utf8,
    "allowsInstallments": pl.Boolean,
    "updatedAt": pl.Utf8,
    "ingested_at": pl.Utf8,
}

files = sorted(_glob.glob(bills_glob))

if not files:
    # No raw bills partition (the glob matched nothing). Bills are rebuilt every run
    # (SCD1 overwrite), so ship an empty, correctly-typed table — the column contract is known
    # in-script (mirrors raw2cleansed_accounts.py's empty-glob guard).
    latest = pl.DataFrame(schema=OUTPUT_SCHEMA)
    write_delta_overwrite("cleansed", "bills", latest)
    print("bills: 0 rows written to cleansed (no raw partition)")
    raise SystemExit(0)

df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")

# Pluggy omits a key when no bill in the fetch carries it, and the raw writer only emits
# columns that appear — project each carried column defensively as typed NULL (all-VARCHAR raw).
for col in OUTPUT_SCHEMA:
    if col not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(col))

# Bills are SCD Type 1 (latest-per bill id, no history). /bills returns the full closed-statement
# set each fetch; latest-per-id across all raw partitions is the current state (a re-paid/updated
# bill picks up its newest state) — safe to drop and rebuild every run. payments/financeCharges
# stay as their raw repr-string blobs (parsed in curated). allowsInstallments is the all-VARCHAR
# raw "True"/"False" string (str(bool) from the parquet writer) — Polars cannot cast Utf8→Boolean
# directly, so derive it NULL-safe: null stays null, else True iff the string is exactly "True".
latest = (
    df.sort("ingested_at", descending=True)
    .unique(subset=["id"], keep="first", maintain_order=True)
    .select(
        "id",
        "accountId",
        "dueDate",
        pl.col("totalAmount").cast(pl.Float64, strict=False).alias("totalAmount"),
        "totalAmountCurrencyCode",
        pl.col("minimumPaymentAmount").cast(pl.Float64, strict=False).alias("minimumPaymentAmount"),
        "financeCharges",
        "payments",
        pl.when(pl.col("allowsInstallments").is_null())
        .then(None)
        .otherwise(pl.col("allowsInstallments") == "True")
        .alias("allowsInstallments"),
        "updatedAt",
        "ingested_at",
    )
)

write_delta_overwrite("cleansed", "bills", latest)

print(f"bills: {len(latest)} rows written to cleansed")
