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
exec(open(Path(__file__).parent / "functions" / "functions.py", encoding="utf-8").read())

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
     # card_outstanding = balance). Set after the charges are generated (unpaid bill + open cycle),
     # and availableCreditLimit = creditLimit - balance with it.
     "name": "Synthetic Card", "balance": 0.0, "currencyCode": "BRL",
     "subtype": "CREDIT_CARD",
     "creditData": {"availableCreditLimit": 8800.0, "creditLimit": 10000.0,
                    "minimumPayment": 120.0, "level": "PLATINUM"}},
]

# --- transactions + bills: ~6 months of a coherent personal cash flow ---
# BANK: one salary a month (the only income), purchases paid from the account, and the card bill
# paid on its due date. CREDIT: purchases (POSITIVE amount, Pluggy convention; curated flips the
# sign) linked to their bill by creditCardMetadata.billId, plus one installment series. Each closed
# bill totals its own charges, so fatura, card balance and the Cartão surface reconcile.
MERCHANTS = ["Mercado Central", "Posto Shell", "Farmacia Sao Joao",
             "Restaurante Bom Sabor", "Streaming PlusTV", "Livraria Cultura"]
CATEGORIES = ["Groceries", "Transport", "Health", "Dining", "Subscriptions", "Shopping"]
# Pluggy categoryId codes (the curated layer flags "05100000" as a credit-card-payment transfer).
CATEGORY_IDS = ["07020000", "07010000", "10000000", "07050000", "13000000", "09000000"]
CC_PAYMENT_CATEGORY_ID = "05100000"
SALARY = 6500.00
SALARY_DAY = 5
P_BANK_PURCHASE = 0.35                        # chance of a purchase paid from the account, per day
P_COUNTERPARTY = 0.25                         # chance an account purchase carries paymentData
P_CARD_PURCHASE = 0.6                         # chance of a card purchase, per day
EMPLOYER = "Empresa Sintetica LTDA"           # payer name -> income_source_key (no document)
# A handful of synthetic counterparties (CNPJ/CPF) so dim_counterparty has rows. The document is
# the join grain; PJ rows carry a name, PF rows do not (mirrors live data).
COUNTERPARTIES = [
    {"name": "Mercado Central LTDA", "document": "12345678000190", "type": "CNPJ"},
    {"name": "Posto Shell LTDA", "document": "98765432000155", "type": "CNPJ"},
    {"name": None, "document": "11122233344", "type": "CPF"},
]
# Bills: m = 0 is the current cycle (due today, unpaid); m >= 1 are closed and paid in full.
CLOSING_DAYS = 7                              # a bill closes 7 days before it is due
DUES = [ANCHOR - timedelta(days=30 * m) for m in range(MONTHS_BACK)]


def bill_of(d):
    """Index of the bill a card charge dated d falls in, or None for the still-open cycle."""
    for m, due in enumerate(DUES):
        closing = due - timedelta(days=CLOSING_DAYS)
        if closing - timedelta(days=30) < d <= closing:
            return m
    return None


transactions = []


def add(acct, d, amount, txn_type, description, **extra):
    """Append one Pluggy-shaped transaction. extra: cat_index (merchant/category), or category
    and categoryId given directly, plus any other raw field (paymentData, creditCardMetadata)."""
    cat_index = extra.pop("cat_index", None)
    rec = {
        "id": f"txn-{len(transactions) + 1:05d}", "accountId": acct["id"], "accountName": acct["name"],
        "amount": round(amount, 2), "currencyCode": "BRL", "date": d.isoformat(),
        "description": description, "descriptionRaw": description.upper(),
        "category": CATEGORIES[cat_index] if cat_index is not None else extra.pop("category", None),
        "categoryId": CATEGORY_IDS[cat_index] if cat_index is not None else extra.pop("categoryId", None),
        "merchant": MERCHANTS[cat_index] if cat_index is not None else None,
        "type": txn_type, "status": "POSTED", "operationType": None,
    }
    rec.update(extra)
    transactions.append(rec)
    return rec


bank, card = accounts
# Card charges per bill index; the key None is the still-open cycle (no billId yet).
charges = {m: 0.0 for m in [*range(MONTHS_BACK), None]}


def card_charge(d, amount, description, cat_index, meta=None):
    amount = round(amount, 2)                 # the bill totals the same cents the charges carry
    m = bill_of(d)
    charges[m] += amount
    meta = dict(meta or {})
    if m is not None:
        meta["billId"] = f"bill-{m:03d}"
    add(card, d, amount, "DEBIT", description, cat_index=cat_index, creditCardMetadata=meta or None)


d = ANCHOR - timedelta(days=MONTHS_BACK * 30)
while d <= ANCHOR:
    if d.day == SALARY_DAY:
        add(bank, d, SALARY, "CREDIT", "SALARIO", category="Salary", categoryId="01010000",
            paymentData={"paymentMethod": "TED", "payer": {"name": EMPLOYER}})
    if rng.random() < P_BANK_PURCHASE:        # purchase paid from the account
        i = rng.randrange(len(MERCHANTS))
        rec = add(bank, d, -rng.uniform(15, 350), "DEBIT", MERCHANTS[i], cat_index=i)
        # Some account purchases carry a counterparty via paymentData (-> dim_counterparty).
        if rng.random() < P_COUNTERPARTY:
            cp = COUNTERPARTIES[rng.randrange(len(COUNTERPARTIES))]
            rec["paymentData"] = {
                "paymentMethod": "PIX",
                "receiver": {"name": cp["name"],
                             "documentNumber": {"value": cp["document"], "type": cp["type"]}},
            }
    if rng.random() < P_CARD_PURCHASE:        # card purchase
        i = rng.randrange(len(MERCHANTS))
        card_charge(d, rng.uniform(8, 250), MERCHANTS[i], i)
    d += timedelta(days=1)

# One installment series: 6x of 300.00, the k-th parcela 30 days after the previous one.
PURCHASE = ANCHOR - timedelta(days=100)
for k in range(1, 7):
    dk = PURCHASE + timedelta(days=30 * (k - 1))
    if dk <= ANCHOR:
        card_charge(dk, 300.00, f"Moveis Sinteticos {k:02d}/06", 5,
                    {"installmentNumber": k, "totalInstallments": 6,
                     "purchaseDate": PURCHASE.isoformat()})

bills = []
for m, due in enumerate(DUES):
    total = round(charges[m], 2)
    bill = {
        "id": f"bill-{m:03d}", "accountId": card["id"], "accountName": card["name"],
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
        pay_day = due - timedelta(days=2)
        bill["payments"] = [{"amount": total, "valueType": "FULL_PAYMENT",
                             "paymentDate": pay_day.isoformat() + "T00:00:00.000Z"}]
        # Both legs of the payment: out of the account, and the credit on the card (its billId).
        add(bank, pay_day, -total, "DEBIT", "PAGAMENTO FATURA CARTAO",
            category="Credit card payment", categoryId=CC_PAYMENT_CATEGORY_ID)
        add(card, pay_day, -total, "CREDIT", "PAGAMENTO RECEBIDO",
            category="Credit card payment", categoryId=CC_PAYMENT_CATEGORY_ID,
            creditCardMetadata={"billId": bill["id"]})
    bills.append(bill)

# The card balance is what is still owed: the unpaid current bill plus the open cycle.
card_owed = round(charges[0] + charges[None], 2)
card["balance"] = card_owed
card["creditData"]["availableCreditLimit"] = round(card["creditData"]["creditLimit"] - card_owed, 2)

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
