# generate_synthetic_data.py — seeded synthetic raw partitions for GetFinance-Show.
# Replaces the Pluggy fetch: runs with NO credentials. Deterministic (fixed seed + anchor).
# Run via the engine as the `fetch` task, or standalone:
#   python3 scripts/generate_synthetic_data.py --target raw
#
# Every value emitted here is FABRICATED. Field names mirror the real Pluggy
# /accounts /transactions /bills /investments payload shapes — they are the contract the
# downstream raw2cleansed*/cleansed2curated* transforms read (do NOT change the transforms;
# add fields here instead). Holding ids below are mapped in config/investments.yaml so they
# flow to dim_investment.
#
# NOTE (S4): this script is at scripts/ depth 1; functions._workspace_root() resolves the
#   workspace via its ancestor-walk FALLBACK (the depth-2 fast path overshoots here). Do NOT
#   "optimize" _workspace_root to the fast path.
import argparse
import random
from datetime import date, timedelta
from pathlib import Path

# Reuse the workspace's IO + partition helpers (same mechanism landing2raw.py uses: exec the
# functions module in this namespace). This script is at scripts/ depth 1, so functions.py is
# one level shallower than for the depth-2 landing/raw2cleansed scripts.
exec(open(Path(__file__).parent / "functions" / "functions.py").read())  # noqa: S102

SEED = 20260621
ANCHOR = date(2026, 6, 21)        # fixed; do NOT use datetime.now() (determinism)
MONTHS_BACK = 6
rng = random.Random(SEED)

parser = argparse.ArgumentParser()
parser.add_argument("--target", required=True)
args = parser.parse_args()

ingestion_date = ANCHOR
ingested_at = ingestion_date.isoformat()

# --- accounts: 1 BANK + 1 CREDIT, Pluggy /accounts shape ---
# bankData carries transferNumber (a Python-repr blob in the raw layer); its first '/'-segment is
# the 3-digit COMPE bank code (001 -> "Banco do Brasil" via config/institutions.yaml) and its last
# segment supplies the account_uuid last4. creditData is a repr blob parsed in curated (U4 reads
# availableCreditLimit / creditLimit / minimumPayment).
accounts = [
    {"id": "acc-bank-0001", "itemId": "item-show-0001", "type": "BANK",
     "name": "Synthetic Checking", "balance": 5230.55, "currencyCode": "BRL",
     "subtype": "CHECKING_ACCOUNT", "number": "00012345-2",
     "bankData": {"transferNumber": "001/1620/00012345-2"}},
    {"id": "acc-cred-0001", "itemId": "item-show-0001", "type": "CREDIT",
     # balance = outstanding owed, POSITIVE (Pluggy convention; fact_balance_snapshot maps
     # card_outstanding = balance, and the report does saldo = cash - card_outstanding). It is
     # internally consistent with the credit limits below: creditLimit 10000 - available 8800 = 1200.
     "name": "Synthetic Card", "balance": 1200.00, "currencyCode": "BRL",
     "subtype": "CREDIT_CARD",
     "creditData": {"availableCreditLimit": 8800.0, "creditLimit": 10000.0,
                    "minimumPayment": 120.0, "level": "PLATINUM"}},
]

# --- transactions: ~6 months daily-ish for both accounts ---
MERCHANTS = ["Mercado Central", "Posto Shell", "Farmacia Sao Joao",
             "Restaurante Bom Sabor", "Streaming PlusTV", "Livraria Cultura"]
CATEGORIES = ["Groceries", "Transport", "Health", "Dining", "Subscriptions", "Shopping"]
# Pluggy categoryId codes (the curated layer flags "05100000" as a credit-card-payment transfer).
CATEGORY_IDS = ["07020000", "07010000", "10000000", "07050000", "13000000", "09000000"]
# A handful of synthetic counterparties (CNPJ/CPF) so dim_counterparty has rows. The document is
# the join grain; PJ rows carry a name, PF rows do not (mirrors live data).
COUNTERPARTIES = [
    {"name": "Mercado Central LTDA", "document": "12345678000190", "type": "CNPJ"},
    {"name": "Posto Shell LTDA", "document": "98765432000155", "type": "CNPJ"},
    {"name": None, "document": "11122233344", "type": "CPF"},
]
transactions = []
n = 0
for acct in accounts:
    d = ANCHOR - timedelta(days=MONTHS_BACK * 30)
    while d <= ANCHOR:
        if rng.random() < 0.7:               # ~70% of days have a txn
            i = rng.randrange(len(MERCHANTS))
            # Pluggy reports credit-card PURCHASES as POSITIVE `amount`; cleansed2curated flips the
            # CREDIT sign (amount_signed = -amount) so a charge lands as a negative expense. Emit
            # positive charges here (a purchase = money out, type DEBIT). BANK keeps signed flow
            # (negative = outflow). Emitting CREDIT amounts negative would invert the sign and make
            # card spend read as income downstream.
            if acct["type"] == "CREDIT":
                amt = round(rng.uniform(8, 450), 2)
                txn_type = "DEBIT"
            else:
                amt = round(rng.uniform(-900, 1800), 2)
                txn_type = "DEBIT" if amt < 0 else "CREDIT"
            n += 1
            rec = {
                "id": f"txn-{n:05d}", "accountId": acct["id"],
                "accountName": acct["name"], "amount": amt, "currencyCode": "BRL",
                "date": d.isoformat(), "description": MERCHANTS[i],
                "descriptionRaw": MERCHANTS[i].upper(),
                "category": CATEGORIES[i], "categoryId": CATEGORY_IDS[i],
                "merchant": MERCHANTS[i], "type": txn_type,
            }
            # ~25% of DEBITs carry a real counterparty via paymentData (parsed to the frozen
            # counterparty_* columns in raw2cleansed.py -> dim_counterparty).
            if txn_type == "DEBIT" and rng.random() < 0.25:
                cp = COUNTERPARTIES[rng.randrange(len(COUNTERPARTIES))]
                rec["paymentData"] = {
                    "paymentMethod": "PIX",
                    "receiver": {
                        "name": cp["name"],
                        "documentNumber": {"value": cp["document"], "type": cp["type"]},
                    },
                }
            transactions.append(rec)
        d += timedelta(days=1)

# --- bills: monthly statements for the CREDIT account ---
# payments is a repr-list blob parsed in fact_card_statement.py (sums amount, derives payment_type
# / paid_in_full from valueType). Older bills are fully paid; the current cycle is unpaid.
bills = []
for m in range(MONTHS_BACK):
    due = ANCHOR - timedelta(days=30 * m)
    total = round(rng.uniform(400, 2200), 2)
    bill = {
        "id": f"bill-{m:03d}", "accountId": "acc-cred-0001",
        "accountName": "Synthetic Card",
        "dueDate": due.isoformat() + "T00:00:00.000Z",
        "totalAmount": total, "totalAmountCurrencyCode": "BRL",
        "minimumPaymentAmount": round(total * 0.15, 2),
        "financeCharges": [],
        "allowsInstallments": True,
        "updatedAt": due.isoformat() + "T03:00:00.000Z",
    }
    if m == 0:
        bill["payments"] = []                       # current cycle: unpaid
    else:
        bill["payments"] = [{
            "amount": total, "valueType": "FULL_PAYMENT",
            "paymentDate": (due - timedelta(days=2)).isoformat() + "T00:00:00.000Z",
        }]
    bills.append(bill)

# --- investments: 3 FIXED_INCOME holdings (ids MUST match config/investments.yaml) ---
# amountOriginal = invested principal; balance = current value (fact_investment_position derives
# return = balance - amountOriginal). issuer/institution are descriptive carry-throughs.
INVESTMENT_IDS = ["inv-cdb-0001", "inv-cdb-0002", "inv-tesouro-0001"]
INVESTMENT_NAMES = ["CDB Synthetic Bank A", "CDB Synthetic Bank B", "Tesouro Synthetic 2030"]
INVESTMENT_SUBTYPES = ["CDB", "CDB", "TREASURY"]
investments = []
for k in range(3):
    invested = round(rng.uniform(1000, 25000), 2)
    current = round(invested * rng.uniform(1.0, 1.18), 2)
    investments.append({
        "id": INVESTMENT_IDS[k], "itemId": "item-show-0001",
        "type": "FIXED_INCOME", "subtype": INVESTMENT_SUBTYPES[k],
        "name": INVESTMENT_NAMES[k],
        "issuer": "Synthetic Issuer S.A.", "institution": None,
        "balance": current, "amountOriginal": invested,
        "currencyCode": "BRL",
    })

# --- stamp lineage + write partitions (same helper landing2raw.py uses) ---
for rec in accounts + transactions + bills + investments:
    rec["ingested_at"] = ingested_at

for entity, records in [("accounts", accounts), ("transactions", transactions),
                        ("bills", bills), ("investments", investments)]:
    out = write_parquet_partition(args.target, entity, records, ingestion_date)
    print(f"{entity}: {len(records)} -> {out}")
