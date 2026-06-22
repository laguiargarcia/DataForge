import argparse
from datetime import datetime, timezone
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--target", required=True)
args = parser.parse_args()

now = datetime.now(timezone.utc)
ingestion_date = now.date()
# A16: ingested_at is day-granular ("YYYY-MM-DD"), not a full timestamp. It is the single
# carried lineage + cross-day dedup-ordering key; nothing downstream consumes sub-day
# precision (snapshot_date truncates to [:10]) and raw is one-overwriting-partition-per-day,
# so a same-day cross-partition tie can't occur. The hive dir key `ingestion_date=` stays the
# physical partition layout; no `ingestion_date` data column is introduced.
ingested_at = ingestion_date.isoformat()
all_accounts: list = []
all_transactions: list = []
all_bills: list = []
all_investments: list = []

for item_id in ITEMS_ID:
    accounts_resp = list_accounts(item_id)
    if accounts_resp is None:
        print(f"Skipping item {item_id}: failed to fetch accounts")
        continue
    accounts = accounts_resp.get("results", [])
    all_accounts.extend(accounts)
    for account in accounts:
        txn_resp = list_transactions(account["id"])
        if txn_resp is None:
            print(f"Skipping account {account['id']}: failed to fetch transactions")
            continue
        for txn in txn_resp.get("results", []):
            txn["accountId"] = account["id"]
            txn["accountName"] = account.get("name", "")
            all_transactions.append(txn)

        # Bills (credit-card statements) only exist for CREDIT accounts; a BANK account's
        # /bills returns total:0. The bill payload has NO native accountId, so tag it here
        # the same way transactions are tagged.
        if account.get("type") == "CREDIT":
            bills_resp = list_bills(account["id"])
            if bills_resp is None:
                print(f"Skipping account {account['id']}: failed to fetch bills")
                continue
            for bill in bills_resp.get("results", []):
                bill["accountId"] = account["id"]
                bill["accountName"] = account.get("name", "")
                all_bills.append(bill)

    inv_resp = list_investments(item_id)
    if inv_resp is None:
        print(f"Skipping item {item_id}: failed to fetch investments")
        continue
    for inv in inv_resp.get("results", []):
        inv["itemId"] = item_id
        all_investments.append(inv)

for rec in all_accounts + all_transactions + all_bills + all_investments:
    rec["ingested_at"] = ingested_at

if all_accounts:
    out = write_parquet_partition(args.target, "accounts", all_accounts, ingestion_date)
    print(f"accounts: {len(all_accounts)} -> {out}")
if all_transactions:
    out = write_parquet_partition(args.target, "transactions", all_transactions, ingestion_date)
    print(f"transactions: {len(all_transactions)} -> {out}")
if all_bills:
    out = write_parquet_partition(args.target, "bills", all_bills, ingestion_date)
    print(f"bills: {len(all_bills)} -> {out}")
if all_investments:
    out = write_parquet_partition(args.target, "investments", all_investments, ingestion_date)
    print(f"investments: {len(all_investments)} -> {out}")
