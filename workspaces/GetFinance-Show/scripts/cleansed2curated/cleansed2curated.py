import argparse
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())  # noqa: S102

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

for _carried in ("status", "txn_type", "operation_type"):
    if _carried not in transactions.columns:
        raise RuntimeError(
            "cleansed.transactions missing v3 carried columns (status/txn_type/"
            "operation_type) — run reprocess.py --entity transactions to backfill (df-668.1)."
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
        row["status"], row["txn_type"], row["operation_type"],
        row["counterparty_name"],
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
        "status": pl.Utf8,
        "txn_type": pl.Utf8,
        "operation_type": pl.Utf8,
        "_counterparty_name": pl.Utf8,
    },
    orient="row",
)

# df-668.1 (E4): cash-basis gate — a row is settled cash iff it hit a BANK account and POSTED.
df = df.with_columns(
    cash_flag=((pl.col("account_type") == "BANK") & (pl.col("status") == "POSTED")).fill_null(False)
)

# df-dnc + E2b: structural etl_tag (E3 precedence step 1 — user rules can never override).
INVESTMENT_CATEGORY_PREFIX = "03"          # Pluggy 03xxxxxx = Investments family
RESGATE_OPERATION_TYPE = "RESGATE_APLIC_FINANCEIRA"
MIRROR_WINDOW_DAYS = 3                     # card-side payment leg within ±3 days of the BANK leg

df = df.with_columns(
    etl_tag=pl.when(
        pl.col("categoryId").str.starts_with(INVESTMENT_CATEGORY_PREFIX)
        | (pl.col("operation_type") == RESGATE_OPERATION_TYPE)
    )
    .then(pl.lit("investment"))
    .when((pl.col("account_type") == "BANK") & (pl.col("categoryId") == CC_PAYMENT_CATEGORY_ID))
    .then(pl.lit("card_payment_bank"))
    .when(
        (pl.col("account_type") == "CREDIT")
        & (pl.col("txn_type") == "CREDIT")
        & (pl.col("categoryId") == CC_PAYMENT_CATEGORY_ID)
    )
    .then(pl.lit("card_payment_card"))
    .otherwise(None)
)

# Inter shape (Finding B): the card-side leg is mislabeled (Shopping/08000000), so mirror-match
# untagged CREDIT-account inflows against BANK payment legs: equal |amount|, within 3 days.
# Known limitation (final review): a genuine refund whose |amount| coincides to the centavo
# with a bill payment within the ±3-day window gets tagged card_payment_card and escapes
# df-0fc netting; accepted (documented) — Saúde unaffected (both rows non-cash), Cartão
# surface risk only.
_bank_legs = df.filter(pl.col("etl_tag") == "card_payment_bank").select(
    pl.col("amount").abs().round(2).alias("_pay_abs"),
    pl.col("date").alias("_pay_date"),
)
_mirror_ids = (
    df.filter(
        (pl.col("account_type") == "CREDIT")
        & (pl.col("txn_type") == "CREDIT")
        & pl.col("etl_tag").is_null()
    )
    .select("id", pl.col("amount").abs().round(2).alias("_pay_abs"), "date")
    .join(_bank_legs, on="_pay_abs")
    .filter((pl.col("date") - pl.col("_pay_date")).dt.total_days().abs() <= MIRROR_WINDOW_DAYS)["id"]
    .unique()
)
df = df.with_columns(
    etl_tag=pl.when(pl.col("id").is_in(_mirror_ids.implode()))
    .then(pl.lit("card_payment_card"))
    .otherwise(pl.col("etl_tag"))
)

# df-0fc: reversal netting. Candidates: CREDIT-account rows with a cc_bill_id, not payment
# legs. Within (cc_bill_id, |amount|), pair k = min(#DEBIT, #CREDIT) rows per side, earliest
# first (date, id) — both members of a pair get netting_applied=True. Flag, never drop.
_cand = (
    df.filter(
        (pl.col("account_type") == "CREDIT")
        & pl.col("cc_bill_id").is_not_null()
        & pl.col("etl_tag").is_null()
    )
    .select("id", "cc_bill_id", "txn_type", "date",
            pl.col("amount").abs().round(2).alias("_amount_abs"))
    .sort(["cc_bill_id", "_amount_abs", "txn_type", "date", "id"])
    .with_columns(
        _rank=pl.int_range(pl.len()).over(["cc_bill_id", "_amount_abs", "txn_type"]) + 1
    )
)
_pairs = (
    _cand.group_by(["cc_bill_id", "_amount_abs"])
    .agg(
        _n_debit=(pl.col("txn_type") == "DEBIT").sum(),
        _n_credit=(pl.col("txn_type") == "CREDIT").sum(),
    )
    .with_columns(_k=pl.min_horizontal("_n_debit", "_n_credit"))
)
_netted_ids = (
    _cand.join(_pairs.select("cc_bill_id", "_amount_abs", "_k"), on=["cc_bill_id", "_amount_abs"])
    .filter(pl.col("_rank") <= pl.col("_k"))["id"]
)
df = df.with_columns(netting_applied=pl.col("id").is_in(_netted_ids.implode()))

# df-668.2 (D2 default): income source key — cleaned receiver on untagged BANK inflows.
# counterparty_name preferred (the payer), fallback etl_merchant_key; whitespace collapsed.
df = df.with_columns(
    income_source_key=pl.when(
        (pl.col("account_type") == "BANK")
        & (pl.col("txn_type") == "CREDIT")
        & pl.col("etl_tag").is_null()
    )
    .then(
        pl.coalesce(pl.col("_counterparty_name"), pl.col("etl_merchant_key"))
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
    )
    .otherwise(None)
).drop("_counterparty_name")

write_delta_overwrite(args.target, "fact_transaction", df)

print(f"fact_transaction: {len(df)} rows -> curated")
