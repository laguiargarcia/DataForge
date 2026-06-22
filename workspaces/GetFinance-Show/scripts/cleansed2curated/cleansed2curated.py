import argparse
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

# Pluggy taxonomy code for "Credit card payment" — tags both bill-settlement legs
# (checking-side debit + card-side payment) identically across any institution.
CC_PAYMENT_CATEGORY_ID = "05100000"


parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# account_uuid comes from dim_account (inner join — transactions whose accountId is
# unmapped/quarantined at U3 have no stable identity and are correctly excluded from the
# fact, mirroring U4/U7).
transactions = read_delta_table(args.source, "transactions")
dim_account = read_delta_table(args.target, "dim_account")

# df-s65 (A5 freeze): description_normalized + etl_merchant_key are now FROZEN at the cleansed
# insert (raw2cleansed.py) and carried here. Fail fast rather than silently re-deriving (which
# would violate A5) when an old cleansed table predates the freeze.
for _frozen in ("description_normalized", "etl_merchant_key"):
    if _frozen not in transactions.columns:
        raise RuntimeError(
            "cleansed.transactions missing frozen columns "
            "(description_normalized/etl_merchant_key) — run reprocess.py --entity "
            "transactions to backfill (df-s65)."
        )

joined = transactions.join(
    dim_account.select(
        pl.col("pluggy_account_id"),
        pl.col("account_uuid"),
    ),
    left_on="accountId",
    right_on="pluggy_account_id",
    how="inner",
)

fact_rows = []
for row in joined.iter_rows(named=True):
    account_type = row["account_type"]
    amount = row["amount"]
    categoryId = row["categoryId"]
    amount_signed = -amount if account_type == "CREDIT" else amount
    is_transfer = categoryId == CC_PAYMENT_CATEGORY_ID
    # df-s65 (A5 freeze): carry the frozen cleansed columns, do NOT re-derive.
    description_normalized = row["description_normalized"]
    etl_merchant_key = row["etl_merchant_key"]
    fact_rows.append((
        row["id"], row["date"], row["account_uuid"], account_type, etl_merchant_key,
        description_normalized, amount, amount_signed, row["category"], categoryId, is_transfer,
        row["counterparty_document"], row["payment_method"],
        row["cc_bill_id"], row["cc_installment_number"],
        row["cc_total_installments"], row["cc_purchase_date"],
    ))

# Event fact (one row per transaction id) — rebuilt each run from the accumulating cleansed
# layer (which UNION-preserves aged-out ids). Unlike the snapshot facts it is DROP+CREATE,
# not append-only: durability lives upstream, exactly as dim_account is rebuilt each run.
df = pl.DataFrame(
    fact_rows,
    schema={
        "id": pl.Utf8,
        "date": pl.Date,
        "account_uuid": pl.Utf8,
        "account_type": pl.Utf8,
        "etl_merchant_key": pl.Utf8,
        "description_normalized": pl.Utf8,
        "amount": pl.Float64,
        "amount_signed": pl.Float64,
        "category": pl.Utf8,
        "categoryId": pl.Utf8,
        "is_transfer": pl.Boolean,
        "counterparty_document": pl.Utf8,
        "payment_method": pl.Utf8,
        "cc_bill_id": pl.Utf8,
        "cc_installment_number": pl.Int64,
        "cc_total_installments": pl.Int64,
        "cc_purchase_date": pl.Date,
    },
    orient="row",
)

write_delta_overwrite(args.target, "fact_transaction", df)

print(f"fact_transaction: {len(df)} rows -> curated")
