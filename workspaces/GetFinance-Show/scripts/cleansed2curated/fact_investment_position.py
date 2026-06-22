import argparse
import polars as pl
from datetime import date
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# holding_uuid comes from dim_investment (inner join — holdings without a stable identity, i.e.
# unmapped/quarantined at U6, are correctly excluded from the fact). A holding that matured/sold
# is simply absent from src.investments today, so the join yields no row and no fact is written
# for it this date — an honest gap, never a carried-forward or fabricated value (A11).
investments = read_delta_table(args.source, "investments")
dim_investment = read_delta_table(args.target, "dim_investment")

joined = investments.join(
    dim_investment.select(
        pl.col("pluggy_holding_id"),
        pl.col("holding_uuid"),
    ),
    left_on="id",
    right_on="pluggy_holding_id",
    how="inner",
)

fact_rows = []
for row in joined.iter_rows(named=True):
    holding_uuid = row["holding_uuid"]
    amount_original = row["amountOriginal"]
    balance = row["balance"]
    currency = row["currency"]
    ingested_at = row["ingested_at"]
    snapshot_date = date.fromisoformat(str(ingested_at)[:10])
    invested_amount = amount_original
    current_value = balance
    ret = None
    if invested_amount is not None and current_value is not None:
        ret = current_value - invested_amount
    fact_rows.append((holding_uuid, snapshot_date, invested_amount, current_value, ret, currency))

# Append-only snapshot fact: NEVER dropped (a past position has no source — A11). Upsert by
# (holding_uuid, snapshot_date): same-day re-run replaces today's rows; prior dates untouched.
df = pl.DataFrame(
    fact_rows,
    schema={
        "holding_uuid": pl.Utf8,
        "snapshot_date": pl.Date,
        "invested_amount": pl.Float64,
        "current_value": pl.Float64,
        "return": pl.Float64,
        "currency": pl.Utf8,
    },
    orient="row",
)

upsert_delta_table(args.target, "fact_investment_position", df, ["holding_uuid", "snapshot_date"])

print(f"fact_investment_position: {len(df)} rows this run -> curated")
