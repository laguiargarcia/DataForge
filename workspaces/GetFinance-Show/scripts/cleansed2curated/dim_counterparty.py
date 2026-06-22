import argparse
from collections import Counter
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

# dim_counterparty (df-pzu) — one row per counterparty document, from the frozen cleansed
# counterparty columns. Mirrors dim_account/dim_investment: read cleansed, dedup, DROP+CREATE.
# Minimal 3-column dim — counts/recency derive in-workbook from fact_transaction when needed.
parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

transactions = read_delta_table(args.source, "transactions").to_dicts()

# Group rows by document; keep only rows with a real counterparty document.
by_doc = {}
for r in transactions:
    doc = r.get("counterparty_document")
    if not doc:
        continue
    by_doc.setdefault(doc, []).append(r)

rows = []
for doc in sorted(by_doc):
    items = by_doc[doc]
    # Canonical name = most frequent non-null name (CPF persons have none -> stays null).
    # Tie-break alphabetically so the dim is deterministic across runs.
    names = [i["counterparty_name"] for i in items if i.get("counterparty_name")]
    name = min(Counter(names).items(), key=lambda kv: (-kv[1], kv[0]))[0] if names else None
    types = [i["counterparty_document_type"] for i in items if i.get("counterparty_document_type")]
    doc_type = min(Counter(types).items(), key=lambda kv: (-kv[1], kv[0]))[0] if types else None
    rows.append({"counterparty_document": doc, "counterparty_name": name,
                 "counterparty_document_type": doc_type})

dim = pl.DataFrame(
    rows,
    schema={
        "counterparty_document": pl.Utf8,
        "counterparty_name": pl.Utf8,
        "counterparty_document_type": pl.Utf8,
    },
)

write_delta_overwrite(args.target, "dim_counterparty", dim)

print(f"dim_counterparty: {len(dim)} rows -> curated")
