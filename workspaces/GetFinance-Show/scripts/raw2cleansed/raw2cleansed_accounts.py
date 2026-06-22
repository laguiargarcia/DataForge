import argparse
import ast
import glob as _glob
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--target", required=True)
args = parser.parse_args()

src_root = _connection_root(args.source)

acc_glob = str(src_root / "accounts" / "ingestion_date=*" / "data.parquet")

# Raw partitions are all-VARCHAR and each fetch only emits the keys present in that fetch, so
# column sets differ per file. diagonal_relaxed unions columns and fills missing as null
# (the Polars equivalent of DuckDB's read_parquet(..., union_by_name=true)).
# Fixed cleansed output schema (mirrors the final .select below): identity/descriptive Utf8,
# balance Float64. number/subtype/transfer_number now flow (D2/D3): the natural-key derivation
# in cleansed2curated_accounts.py reads them (account_uuid last4 from number, kind from subtype,
# institution/bank-code from transfer_number) — A17 "no consumer reads them" no longer holds.
OUTPUT_SCHEMA = {
    "id": pl.Utf8, "itemId": pl.Utf8, "type": pl.Utf8, "subtype": pl.Utf8,
    "number": pl.Utf8, "transfer_number": pl.Utf8, "name": pl.Utf8,
    "balance": pl.Float64, "currency": pl.Utf8, "creditData": pl.Utf8, "ingested_at": pl.Utf8,
}


def _parse_transfer_number(bank_data):
    """transferNumber out of the raw bankData column, a Python-repr string (or null/None) —
    parsed defensively, mirroring how U4 repr-parses creditData. Returns None for a credit
    card / no-bankData account (transferNumber absent), a malformed blob, or a missing column."""
    if bank_data is None:
        return None
    try:
        bd = ast.literal_eval(str(bank_data))
        return bd.get("transferNumber")
    except (ValueError, SyntaxError, AttributeError):
        return None


files = sorted(_glob.glob(acc_glob))

if not files:
    # No raw accounts partition (the glob matched nothing). Accounts are rebuilt every run
    # (SCD1 overwrite), so ship an empty, correctly-typed table — the column contract is known
    # in-script, so downstream U3 sees the same schema either way (mirrors raw2cleansed_investments).
    latest = pl.DataFrame(schema=OUTPUT_SCHEMA)
    write_delta_overwrite("cleansed", "accounts", latest)
    print("accounts: 0 rows written to cleansed (no raw partition)")
    raise SystemExit(0)

df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")

# Pluggy omits a key when no account in the fetch carries it, and the raw writer only emits
# columns that appear — so for a bank-only item creditData is absent, and for a credit-only
# item bankData/subtype/number may be absent. Project each defensively as typed NULL.
for col in ("creditData", "bankData", "subtype", "number"):
    if col not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(col))

# transfer_number: parse it out of the raw bankData repr blob row-by-row (None for credit /
# no-bankData accounts). bankData itself is NOT carried past cleansed — only its transferNumber.
df = df.with_columns(
    pl.col("bankData")
    .map_elements(_parse_transfer_number, return_dtype=pl.Utf8)
    .alias("transfer_number")
)

# Accounts are SCD Type 1 (keep latest, no history). Pluggy returns the full account set
# each fetch, so latest-per-id across all raw partitions is the current state — safe to
# drop and rebuild every run (unlike snapshot facts, which have no replay source).
# itemId is native to the Pluggy payload (institution FK; resolved to a name at U3).
# creditData is carried as a raw blob (Python-repr string); U4 parses availableCreditLimit.
# number/subtype/transfer_number feed the natural-key account_uuid derivation at U3.
latest = (
    df.sort("ingested_at", descending=True)
    .unique(subset=["id"], keep="first", maintain_order=True)
    .select(
        "id",
        "itemId",
        "type",
        pl.col("subtype").cast(pl.Utf8, strict=False).alias("subtype"),
        pl.col("number").cast(pl.Utf8, strict=False).alias("number"),
        pl.col("transfer_number").cast(pl.Utf8, strict=False).alias("transfer_number"),
        "name",
        pl.col("balance").cast(pl.Float64, strict=False).alias("balance"),
        pl.col("currencyCode").alias("currency"),
        "creditData",
        "ingested_at",
    )
)

write_delta_overwrite("cleansed", "accounts", latest)

print(f"accounts: {len(latest)} rows written to cleansed")
