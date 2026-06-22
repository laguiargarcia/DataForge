import argparse
from datetime import date
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed (convention; data deps are curated)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# fact_fatura_mes: ONE row per (credit account, due month) — the workbook's Fatura table
# (2026-06-09 fatura-panel spec). Closed bills come from fact_card_statement (authoritative);
# one OPEN row per account aggregates posted-but-unbilled charges (cc_bill_id NULL). Payments
# always carry the billId of the bill they settle, so they are excluded by construction;
# refunds are negative and reduce the open sum naturally. Deterministic rebuild each run —
# when a bill closes and data refreshes, the open row is replaced by the real bill row.
stmts = read_delta_table(args.target, "fact_card_statement")
fact = read_delta_table(args.target, "fact_transaction")


def _month_after(anomes):
    y, m = divmod(anomes, 100)
    return (y + 1) * 100 + 1 if m == 12 else anomes + 1


def _mes_inicio(anomes):
    # First-of-month date: the dim_calendar join key (fact_budget pattern — any month-level
    # filter includes day 1, so the row is always picked up).
    return date(anomes // 100, anomes % 100, 1)


rows = []

for r in stmts.iter_rows(named=True):
    if r["anomes_key"] is None:
        print(f"fact_fatura_mes: skipping statement {r['statement_id']} — NULL due_date/anomes_key")
        continue
    valor = r["statement_amount"] if r["statement_amount"] is not None else 0.0
    pago = r["paid_amount"] if r["paid_amount"] is not None else 0.0
    rows.append((
        r["account_uuid"], r["anomes_key"], _mes_inicio(r["anomes_key"]), r["due_date"],
        r["statement_id"], valor, pago, max(0.0, valor - pago), False,
    ))

cc = fact.filter(pl.col("account_type") == "CREDIT")
known_bills = stmts.get_column("statement_id").to_list()

# Charges referencing a bill we have not fetched yet belong to that closed bill's month, not
# the open cycle — drop them (transient: bills + transactions land in the same job run).
unmatched = cc.filter(
    pl.col("cc_bill_id").is_not_null() & ~pl.col("cc_bill_id").is_in(known_bills)
)
if unmatched.height:
    print(f"fact_fatura_mes: {unmatched.height} charge(s) reference unknown bills "
          "(transient fetch lag) — skipped")

unbilled = cc.filter(pl.col("cc_bill_id").is_null())

for grp in unbilled.partition_by("account_uuid", maintain_order=True):
    account_uuid = grp.get_column("account_uuid")[0]
    open_sum = float(grp.get_column("amount").sum())
    acct_stmts = stmts.filter(pl.col("account_uuid") == account_uuid)
    max_anomes = acct_stmts.get_column("anomes_key").max() if acct_stmts.height else None
    if max_anomes is not None:
        open_anomes = _month_after(int(max_anomes))
    else:
        # Defensive: no closed bills (or all have NULL anomes_key) — next month after latest charge.
        last_txn = grp.get_column("date").max()
        open_anomes = _month_after(last_txn.year * 100 + last_txn.month)
    rows.append((
        account_uuid, open_anomes, _mes_inicio(open_anomes), None, None,
        open_sum, 0.0, max(0.0, open_sum), True,
    ))

df = pl.DataFrame(
    rows,
    schema={
        "account_uuid": pl.Utf8,
        "anomes_key": pl.Int64,
        "mes_inicio": pl.Date,
        "due_date": pl.Date,
        "statement_id": pl.Utf8,
        "valor_fatura": pl.Float64,
        "valor_pago": pl.Float64,
        "valor_pendente": pl.Float64,
        "is_open": pl.Boolean,
    },
    orient="row",
)

write_delta_overwrite(args.target, "fact_fatura_mes", df)

n_open = df.filter(pl.col("is_open")).height
print(f"fact_fatura_mes: {len(df)} rows ({n_open} open) -> curated")
