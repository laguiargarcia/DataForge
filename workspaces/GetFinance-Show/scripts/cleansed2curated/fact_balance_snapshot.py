import argparse
import ast
import glob as _glob
import polars as pl
from datetime import date
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # raw (all accounts ingestion_date partitions)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# fact_balance_snapshot is a faithful daily projection of raw.accounts: one row per
# (account, snapshot_date) where snapshot_date = ingested_at[:10]. raw is the permanent,
# never-pruned log, so this fact is rebuilt from raw each run (deterministic + replayable).
# Read ALL raw accounts partitions (mirrors raw2cleansed_accounts.py): all-VARCHAR, and each
# fetch only emits keys present in that fetch, so diagonal_relaxed unions columns + null-fills.
src_root = _connection_root(args.source)
acc_glob = str(src_root / "accounts" / "ingestion_date=*" / "data.parquet")
files = sorted(_glob.glob(acc_glob))
accounts = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")

# Pluggy omits creditData on bank-only fetches, so the raw writer may not emit the column at
# all. Project it defensively as typed NULL (mirrors raw2cleansed_accounts.py).
if "creditData" not in accounts.columns:
    accounts = accounts.with_columns(pl.lit(None, dtype=pl.Utf8).alias("creditData"))

# account_uuid comes from dim_account (inner join — accounts without a stable identity, i.e.
# unmapped/quarantined, are correctly excluded from the fact). dim_account is read from the
# target (curated), not the raw source.
dim_account = read_delta_table(args.target, "dim_account")

joined = accounts.join(
    dim_account.select(
        pl.col("pluggy_account_id"),
        pl.col("account_uuid"),
    ),
    left_on="id",
    right_on="pluggy_account_id",
    how="inner",
)


def _parse_date(value):
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


fact_rows = []
for row in joined.iter_rows(named=True):
    account_uuid = row["account_uuid"]
    atype = row["type"]
    balance = float(row["balance"]) if row["balance"] is not None else None
    currency = row["currencyCode"]
    credit_data = row["creditData"]
    snapshot_date = date.fromisoformat(str(row["ingested_at"])[:10])

    available_credit_limit = None
    credit_limit = None
    minimum_payment = None
    statement_close_date = None
    statement_due_date = None
    if credit_data:
        try:
            cd = ast.literal_eval(credit_data)
            available_credit_limit = cd.get("availableCreditLimit")
            credit_limit = cd.get("creditLimit")  # int in payload, cast to Float64 below
            minimum_payment = cd.get("minimumPayment")
            statement_close_date = _parse_date(cd.get("balanceCloseDate"))  # currently always NULL
            statement_due_date = _parse_date(cd.get("balanceDueDate"))
        except (ValueError, SyntaxError, AttributeError):
            pass

    if atype == "CREDIT":
        card_outstanding = balance
        available = None
    else:
        card_outstanding = None
        available = balance

    fact_rows.append((
        account_uuid, snapshot_date, atype, balance, available, card_outstanding,
        available_credit_limit, credit_limit, minimum_payment,
        statement_close_date, statement_due_date, currency,
    ))

# Rebuild from raw each run (DROP+CREATE via write_delta_overwrite): raw is the permanent log,
# so the snapshot has a replay source and the old append-only-upsert assumption is retired.
df = pl.DataFrame(
    fact_rows,
    schema={
        "account_uuid": pl.Utf8,
        "snapshot_date": pl.Date,
        "account_type": pl.Utf8,
        "balance": pl.Float64,
        "available": pl.Float64,
        "card_outstanding": pl.Float64,
        "available_credit_limit": pl.Float64,
        "credit_limit": pl.Float64,
        "minimum_payment": pl.Float64,
        "statement_close_date": pl.Date,
        "statement_due_date": pl.Date,
        "currency": pl.Utf8,
    },
    orient="row",
)

write_delta_overwrite(args.target, "fact_balance_snapshot", df)

print(f"fact_balance_snapshot: {len(df)} rows rebuilt from raw -> curated")
