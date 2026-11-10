import argparse
import ast
import polars as pl
from datetime import date, timedelta
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())  # noqa: S102

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


# --- df-hog (2026-07-02): the 2026-06-17 Itaú/Pluggy shape change broke three assumptions --- #
# (1) a bill's payments[] may carry the PREVIOUS cycle's settlement (OTHER_PAYMENT/BANK_SLIP,
#     exact amount+date of the prior bill's payment) — summing payments[] as-is cross-attributes;
# (2) a statement can be RE-ISSUED under a new bill id (old id still returned; charges re-keyed
#     to the new id) — SCD-1-by-id keeps both, duplicating the due-month;
# (3) a débito-automático settlement never appears on the bill at all — its only records are the
#     BANK 05100000 transaction and, one cycle later, an OTHER_PAYMENT entry on the NEXT bill.
# Fix: dedup re-issued bills (keep the charge-referenced id), pool ALL payment records per
# account (bill entries + BANK settlement legs), dedup identical settlements, and reattribute
# each payment to the bill it actually settles.

_TOL = 0.01                 # money equality tolerance
_SAME_SETTLEMENT_DAYS = 3   # equal amount + dates within this window + DIFFERENT origins = one settlement
_EXACT_WINDOW = (45, 60)    # exact-amount attribution: paymentDate in [due-45d, due+60d] — the wide
                            # lower bound lets an early full payment match its own bill instead of
                            # falling to the ambiguous pass-2 fallback (review finding, 2026-07-02)
_LATE_GRACE_DAYS = 3        # fallback attribution: bill due_date <= paymentDate + grace

# Optional cleansed.transactions slice: charge references (re-issue dedup) + BANK settlement
# legs. Guarded — the table may not exist in minimal/test environments.
if delta_table_exists(args.source, "transactions"):
    _txn = read_delta_table(args.source, "transactions")
    _referenced_bill_ids = set(_txn.get_column("cc_bill_id").drop_nulls().to_list())
    _bank_legs = [
        {"amount": abs(r["amount"]), "date": r["date"], "value_type": None,
         "src": "bank_leg", "origin": "bank"}
        for r in _txn.filter(
            (pl.col("account_type") == "BANK")
            & (pl.col("categoryId") == "05100000")
            & (pl.col("amount") < 0)
        ).select("date", "amount").iter_rows(named=True)
        if r["date"] is not None and r["amount"] is not None
    ]
else:
    _referenced_bill_ids = set()
    _bank_legs = []

# Parse every bill row up front.
parsed_bills = []
for row in joined.iter_rows(named=True):
    due_date = _parse_date(row["dueDate"])
    parsed_bills.append({
        "account_uuid": row["account_uuid"],
        "statement_id": row["id"],
        "due_date": due_date,
        "anomes_key": due_date.year * 100 + due_date.month if due_date is not None else None,
        "statement_amount": _num(row["totalAmount"]),
        "minimum_payment": _num(row["minimumPaymentAmount"]),
        "currency": row["totalAmountCurrencyCode"],
        "allows_installments": row["allowsInstallments"],
        "updated_at": row["updatedAt"],
        "payments": _parse_blob(row["payments"]),
        "finance_charges": _parse_blob(row["financeCharges"]),
    })

# Re-issued-bill dedup: within (account, due-month), multiple bills are one re-issued
# statement — deduped unconditionally (fact_fatura_mes's one-row-per-month contract).
# Keep the id charges reference (the re-issue re-keys charges); tie-break latest updatedAt.
# The dropped twin's payments[] still feeds the pool below.
_by_acct = {}
for b in parsed_bills:
    _by_acct.setdefault(b["account_uuid"], []).append(b)

kept_bills = []
twin_pools = {}  # account_uuid -> dropped twins (payment records still pooled)
for _acct, _blist in sorted(_by_acct.items()):
    _by_month = {}
    for b in _blist:
        _by_month.setdefault(b["anomes_key"], []).append(b)
    for _anomes, _group in _by_month.items():
        if _anomes is None or len(_group) == 1:
            kept_bills.extend(_group)
            continue
        # ONE closed row per (account, due-month) is fact_fatura_mes's contract — dedup
        # unconditionally, even when the re-issue revised the amount (review finding,
        # 2026-07-02); the kept twin's statement_amount wins, and both are logged.
        _ref = [b for b in _group if b["statement_id"] in _referenced_bill_ids]
        _pick = max(_ref or _group, key=lambda b: (b["updated_at"] or ""))
        kept_bills.append(_pick)
        for b in _group:
            if b is not _pick:
                twin_pools.setdefault(_acct, []).append(b)
                print(f"fact_card_statement: dropped re-issued twin {b['statement_id']} "
                      f"(anomes {_anomes}, amount {b['statement_amount']}, kept "
                      f"{_pick['statement_id']} amount {_pick['statement_amount']}) — df-hog")


def _bill_payment_records(bills):
    recs = []
    for b in bills:
        for p in b["payments"]:
            if not isinstance(p, dict):
                continue
            amt = _num(p.get("amount"))
            d = _parse_date(p.get("paymentDate"))
            if amt is None or d is None:
                continue
            recs.append({"amount": amt, "date": d, "value_type": p.get("valueType"),
                         "src": "bill", "origin": b["statement_id"]})
    return recs


def _same_settlement(a, b):
    # Origin-aware (review finding, 2026-07-02): a settlement is cross-recorded only across
    # DIFFERENT origins (its own bill, the NEXT bill's phantom entry, the BANK leg). Two
    # equal payments listed on the SAME bill are genuinely two payments — never collapsed.
    return (a["origin"] != b["origin"]
            and abs(a["amount"] - b["amount"]) <= _TOL
            and abs((a["date"] - b["date"]).days) <= _SAME_SETTLEMENT_DAYS)


fact_rows = []
_bank_legs_remaining = list(_bank_legs)
_unattributed = 0
for _acct, _ in sorted(_by_acct.items()):
    acct_kept = sorted((b for b in kept_bills if b["account_uuid"] == _acct),
                       key=lambda b: (b["due_date"] or date.min, b["statement_id"]))

    # Pool: kept bills' records + dropped twins' records + bank legs; dedup identical
    # settlements (the same payment can be recorded on its own bill, on the NEXT bill,
    # and as a BANK transaction). Bill FULL_PAYMENT records are accepted first so the
    # surviving record keeps type fidelity.
    raw_pool = _bill_payment_records(acct_kept + twin_pools.get(_acct, []))
    raw_pool.sort(key=lambda r: (r["src"] != "bill", r["value_type"] != "FULL_PAYMENT",
                                 r["date"], r["amount"]))
    pool = []
    for rec in raw_pool:
        if not any(_same_settlement(rec, kept) for kept in pool):
            pool.append(rec)
    _leftover_legs = []
    for leg in _bank_legs_remaining:
        if any(_same_settlement(leg, kept) for kept in pool):
            continue  # already recorded via a bill entry — same settlement
        _leftover_legs.append(leg)
    _bank_legs_remaining = _leftover_legs

    # Attribution pass 1 — exact amount match: a payment equal to a bill's statement_amount
    # dated within [due-45d, due+60d] settles that bill (nearest due wins). Bank legs join
    # this pass only (never the fallback: they carry no card-account identity).
    attributed = {b["statement_id"]: [] for b in acct_kept}
    unassigned = []
    for rec in pool + _bank_legs_remaining:
        cands = [
            b for b in acct_kept
            if b["statement_amount"] is not None and b["due_date"] is not None
            and abs(rec["amount"] - b["statement_amount"]) <= _TOL
            and (b["due_date"] - timedelta(days=_EXACT_WINDOW[0])) <= rec["date"]
            <= (b["due_date"] + timedelta(days=_EXACT_WINDOW[1]))
        ]
        if cands:
            best = min(cands, key=lambda b: abs((rec["date"] - b["due_date"]).days))
            attributed[best["statement_id"]].append(rec)
            if rec["src"] == "bank_leg":
                _bank_legs_remaining = [l for l in _bank_legs_remaining if l is not rec]
        elif rec["src"] == "bill":
            unassigned.append(rec)

    # Attribution pass 2 — fallback for partial payments: the bill with the largest
    # due_date <= paymentDate + grace.
    for rec in unassigned:
        cands = [b for b in acct_kept if b["due_date"] is not None
                 and b["due_date"] <= rec["date"] + timedelta(days=_LATE_GRACE_DAYS)]
        if cands:
            best = max(cands, key=lambda b: b["due_date"])
            attributed[best["statement_id"]].append(rec)
        else:
            _unattributed += 1

    for b in acct_kept:
        recs = attributed[b["statement_id"]]
        statement_amount = b["statement_amount"]
        if recs:
            paid_amount = sum(r["amount"] for r in recs)
            paid_date = max(r["date"] for r in recs)
            distinct_types = set(r["value_type"] for r in recs if r["value_type"] is not None)
            if len(distinct_types) > 1:
                payment_type = "MIXED"
            elif distinct_types == {"FULL_PAYMENT"}:
                payment_type = "FULL_PAYMENT"
            elif distinct_types:
                payment_type = "PARTIAL"
            else:
                payment_type = None  # settled via bank leg only — no bill-side valueType
        else:
            paid_amount = None
            paid_date = None
            payment_type = None

        # paid_in_full MUST gate on attributed payments existing: a $0 bill with no payments
        # is False, not True (otherwise 0 >= -0.01 would mislabel it). Tolerance 0.01.
        paid_in_full = bool(
            recs
            and paid_amount is not None
            and statement_amount is not None
            and paid_amount >= statement_amount - _TOL
        )

        # finance_charges: Σ of the amounts in the financeCharges blob. Live data is always
        # empty; the element structure is unconfirmed, so probe plausible keys defensively.
        fc_total = 0.0
        for fc in b["finance_charges"]:
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
            b["account_uuid"], b["statement_id"], b["due_date"], b["anomes_key"],
            statement_amount, b["minimum_payment"], paid_amount, paid_date, payment_type,
            paid_in_full, fc_total, b["allows_installments"], b["currency"], b["updated_at"],
        ))

if _unattributed:
    print(f"fact_card_statement: {_unattributed} payment record(s) matched no bill — ignored")
if _bank_legs_remaining:
    print(f"fact_card_statement: {len(_bank_legs_remaining)} BANK 05100000 settlement leg(s) "
          "matched no bill (amount differs from every statement) — ignored")

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
