import argparse
import ast
import polars as pl
from datetime import date
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed (cleansed.bills, latest-per-id)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# fact_card_statement: one row per (card account, statement cycle / bill id). Reads
# cleansed.bills (already SCD-1 latest-per-bill-id, full closed-statement history) and
# dim_account (curated) for the stable account_uuid. Rebuilt every run with
# write_delta_overwrite (deterministic — cleansed.bills holds the full history).
bills = read_delta_table(args.source, "bills")

# account_uuid comes from dim_account (inner join — bills on accounts without a stable identity
# are excluded, mirroring fact_balance_snapshot.py). dim_account is read from the target (curated).
dim_account = read_delta_table(args.target, "dim_account")

joined = bills.join(
    dim_account.select(
        pl.col("pluggy_account_id"),
        pl.col("account_uuid"),
    ),
    left_on="accountId",
    right_on="pluggy_account_id",
    how="inner",
)


def _parse_date(value):
    # All bill/payment dates are full ISO datetimes (e.g. 2026-05-17T00:00:00.000Z); slice [:10]
    # before date.fromisoformat (the full string throws). NULL-safe.
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _parse_blob(value):
    # payments / financeCharges are carried as repr-string blobs (Python repr of a list). Parse
    # NULL-safe (mirrors creditData handling in fact_balance_snapshot.py). Returns [] on
    # null/empty/malformed so downstream aggregation is always over a list.
    if value is None:
        return []
    try:
        parsed = ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


fact_rows = []
for row in joined.iter_rows(named=True):
    account_uuid = row["account_uuid"]
    statement_id = row["id"]
    due_date = _parse_date(row["dueDate"])
    anomes_key = due_date.year * 100 + due_date.month if due_date is not None else None
    statement_amount = _num(row["totalAmount"])
    minimum_payment = _num(row["minimumPaymentAmount"])
    currency = row["totalAmountCurrencyCode"]
    allows_installments = row["allowsInstallments"]
    updated_at = row["updatedAt"]

    payments = _parse_blob(row["payments"])
    finance_charges = _parse_blob(row["financeCharges"])

    # paid_amount: Σ payments[].amount (NULL if no payments at all). paid_date: max payment date.
    # payment_type: from payments[].valueType — a single FULL_PAYMENT → FULL_PAYMENT; a single
    # non-full → PARTIAL; a mix of distinct types → MIXED; no payments → NULL.
    if payments:
        amounts = [_num(p.get("amount")) for p in payments if isinstance(p, dict)]
        amounts = [a for a in amounts if a is not None]
        paid_amount = sum(amounts) if amounts else 0.0

        pay_dates = [_parse_date(p.get("paymentDate")) for p in payments if isinstance(p, dict)]
        pay_dates = [d for d in pay_dates if d is not None]
        paid_date = max(pay_dates) if pay_dates else None

        value_types = [p.get("valueType") for p in payments if isinstance(p, dict)]
        distinct_types = set(t for t in value_types if t is not None)
        if len(distinct_types) > 1:
            payment_type = "MIXED"
        elif distinct_types == {"FULL_PAYMENT"}:
            payment_type = "FULL_PAYMENT"
        elif distinct_types:
            payment_type = "PARTIAL"
        else:
            payment_type = None
    else:
        paid_amount = None
        paid_date = None
        payment_type = None

    # paid_in_full MUST gate on payments existing: a $0 bill with no payments is False, not True
    # (otherwise 0 >= -0.01 would mislabel it). Tolerance 0.01 absorbs float rounding.
    paid_in_full = bool(
        payments
        and paid_amount is not None
        and statement_amount is not None
        and paid_amount >= statement_amount - 0.01
    )

    # finance_charges: Σ of the amounts in the financeCharges blob. Live data is always empty;
    # the element structure is unconfirmed, so probe a few plausible amount keys defensively.
    fc_total = 0.0
    for fc in finance_charges:
        if isinstance(fc, dict):
            val = fc.get("amount", fc.get("value", fc.get("totalAmount")))
            num = _num(val)
            if num is not None:
                fc_total += num
        else:
            num = _num(fc)
            if num is not None:
                fc_total += num

    fact_rows.append((
        account_uuid, statement_id, due_date, anomes_key, statement_amount,
        minimum_payment, paid_amount, paid_date, payment_type, paid_in_full,
        fc_total, allows_installments, currency, updated_at,
    ))

df = pl.DataFrame(
    fact_rows,
    schema={
        "account_uuid": pl.Utf8,
        "statement_id": pl.Utf8,
        "due_date": pl.Date,
        "anomes_key": pl.Int64,
        "statement_amount": pl.Float64,
        "minimum_payment": pl.Float64,
        "paid_amount": pl.Float64,
        "paid_date": pl.Date,
        "payment_type": pl.Utf8,
        "paid_in_full": pl.Boolean,
        "finance_charges": pl.Float64,
        "allows_installments": pl.Boolean,
        "currency": pl.Utf8,
        "updated_at": pl.Utf8,
    },
    orient="row",
)

write_delta_overwrite(args.target, "fact_card_statement", df)

print(f"fact_card_statement: {len(df)} statements rebuilt from cleansed.bills -> curated")
