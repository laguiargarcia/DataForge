import argparse
import polars as pl
from datetime import date
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())

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
    # df-9uv: prefer Pluggy's own P&L (amountProfit, populated by some banks) — amountOriginal is
    # gross-ever-deposited and never reduced by partial withdrawals, so the arithmetic fallback
    # reads withdrawals as losses. Fallback stays for holdings where Pluggy leaves amountProfit
    # NULL: without a partial withdrawal, current − invested remains correct.
    # .get(): tolerate a cleansed table built before amountProfit was carried (SCD1 rebuild).
    amount_profit = row.get("amountProfit")
    ret = None
    if amount_profit is not None:
        ret = amount_profit
    elif invested_amount is not None and current_value is not None:
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

validate(df, "curated.fact_investment_position", FACT_INVESTMENT_POSITION_RULES, "snapshot_date")
# Reconciliation (df-ahua.4.11): every mapped holding of cleansed is one row of this run.
validate(layer_totals(joined.select("holding_uuid"), df, ["holding_uuid"]),
         "curated.fact_investment_position vs cleansed", ROWS_CARRIED_RULES, "holding_uuid")
upsert_delta_table(args.target, "fact_investment_position", df, ["holding_uuid", "snapshot_date"])

print(f"fact_investment_position: {len(df)} rows this run -> curated")
