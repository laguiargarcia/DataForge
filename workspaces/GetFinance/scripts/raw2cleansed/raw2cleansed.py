import argparse
import ast
import glob as _glob
import os
import re
import yaml
import polars as pl
from datetime import date
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())

# df-s65 (A5 freeze): description_normalized + etl_merchant_key are derived ONCE here at the
# cleansed insert (mirroring the df-pzu counterparty freeze) and carried by cleansed2curated.
# Config-driven merchant_key normalization (multi-institution). Env override mirrors the
# fact_*_snapshot --parquet-path pattern and gives tests a seam.
_norm_config_path = Path(
    os.environ.get("MERCHANT_NORM_CONFIG")
    or str(Path(__file__).parent.parent.parent / "config" / "merchant_normalization.yaml")
)
with open(_norm_config_path, encoding="utf-8") as _f:
    _norm_rules = yaml.safe_load(_f).get("rules", [])


def _rule_applies_to(rule, institution):
    """A10: a rule with no `institution:` key is match-all. A rule WITH `institution:`
    (string or list) is eligible only when `institution` is non-None and in that set.
    `institution=None` => skip the institution filter (match-all, pre-A10 behaviour)."""
    declared = rule.get("institution")
    if declared is None:
        return True
    if institution is None:
        return False
    allowed = [declared] if isinstance(declared, str) else list(declared)
    return institution in allowed


def _normalize_description(description, institution=None):
    """Return the canonical normalized description string (spec §5 description_normalized):
    first matching rule's strips applied, whitespace collapsed. A10-scoped by institution.
    Frozen with institution=None (match-all) — identical output to the pre-freeze curated
    derivation under today's un-scoped config (df-s65)."""
    text = description or ""
    for rule in _norm_rules:
        if not _rule_applies_to(rule, institution):
            continue
        if re.search(rule["match"], text):
            for pat in rule.get("strip", []):
                text = re.sub(pat, "", text)
            break
    return re.sub(r"\s+", " ", text).strip()


def _merchant_name(merchant_value):
    """df-7sa: raw `merchant` is str()-ified at landing, so a nested Pluggy merchant object
    arrives as a Python repr-string like "{'name': 'ACME', 'businessName': 'ACME LTDA'}".
    If it parses to a dict, return name (preferred) or businessName; a non-empty plain string
    is returned unchanged; empty/None/"None" -> None."""
    if merchant_value is None:
        return None
    s = str(merchant_value).strip()
    if not s or s == "None":
        return None
    try:
        obj = ast.literal_eval(s)
    except (ValueError, SyntaxError):
        return s  # plain string, not a repr — use verbatim
    if isinstance(obj, dict):
        return obj.get("name") or obj.get("businessName")
    return s


def _merchant_key(description, merchant_value, counterparty_name):
    """etl_merchant_key precedence (df-pzu): Pluggy `merchant` name -> counterparty name
    (frozen cleansed column) -> normalized description. Frozen with institution=None."""
    name = _merchant_name(merchant_value)
    if name:
        return name
    if counterparty_name:
        return counterparty_name
    return _normalize_description(description)

# df-pzu: 4 frozen counterparty columns parsed once from raw.transactions.paymentData (a Python
# repr string, or "None"). Computed here at cleansed insert (A5) and carried thereafter.
_CP_DTYPE = pl.Struct({
    "counterparty_name": pl.Utf8,
    "counterparty_document": pl.Utf8,
    "counterparty_document_type": pl.Utf8,
    "payment_method": pl.Utf8,
})


# documentNumber.type -> the PF/PJ kind cleansed keeps; any other type is None.
_DOCUMENT_KIND = {"CNPJ": "PJ", "CPF": "PF"}


def _repr_dict(repr_str):
    """Parse a raw column holding a Python repr string. Return the dict, or None when the column
    is empty, the literal "None", malformed, or not a dict."""
    if not repr_str or repr_str == "None":
        return None
    try:
        obj = ast.literal_eval(repr_str)
    except (ValueError, SyntaxError):
        return None
    return obj if isinstance(obj, dict) else None


def _document(side):
    """(value, PF/PJ kind) of a paymentData side's documentNumber; None for what is missing."""
    dn = side.get("documentNumber")
    if not isinstance(dn, dict):
        return None, None
    raw_type = dn.get("type")
    return dn.get("value"), _DOCUMENT_KIND.get(raw_type) if isinstance(raw_type, str) else None


def _counterparty(txn_type, payment_data_str):
    name = doc = doc_type = method = None
    pd_obj = _repr_dict(payment_data_str)
    if pd_obj is not None:
        method = pd_obj.get("paymentMethod")
        # Counterparty = the side OPPOSITE our flow: receiver on a DEBIT (we paid out),
        # payer on a CREDIT (we were paid). NO fallback to the other side: the opposite side is
        # our own account holder, so falling back would label the holder's own CPF as the
        # counterparty on most rows and break the locked acceptance numbers. The chosen side is
        # simply null when no real counterparty.
        side = pd_obj.get("receiver") if txn_type == "DEBIT" else pd_obj.get("payer")
        if isinstance(side, dict):
            name = side.get("name")
            doc, doc_type = _document(side)
    return {"counterparty_name": name, "counterparty_document": doc,
            "counterparty_document_type": doc_type, "payment_method": method}


# Credit-card metadata: 4 frozen columns parsed once from raw.transactions.creditCardMetadata
# (a Python repr string, or "None"). billId links a charge to its closed bill; the installment
# trio identifies parcel purchases (2026-06-09 fatura-panel spec).
_CC_DTYPE = pl.Struct({
    "cc_bill_id": pl.Utf8,
    "cc_installment_number": pl.Int64,
    "cc_total_installments": pl.Int64,
    "cc_purchase_date": pl.Date,
})


def _cc_meta(meta_str):
    bill_id = inst_n = inst_total = purchase = None
    obj = _repr_dict(meta_str)
    if obj is not None:
        bill_id = obj.get("billId")
        inst_n = obj.get("installmentNumber")
        inst_total = obj.get("totalInstallments")
        pd_raw = obj.get("purchaseDate")
        if pd_raw is not None:
            try:
                purchase = date.fromisoformat(str(pd_raw)[:10])
            except ValueError:
                purchase = None
    return {"cc_bill_id": bill_id,
            "cc_installment_number": int(inst_n) if inst_n is not None else None,
            "cc_total_installments": int(inst_total) if inst_total is not None else None,
            "cc_purchase_date": purchase}


parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--target", required=True)
args = parser.parse_args()

src_root = _connection_root(args.source)

txn_glob = str(src_root / "transactions" / "ingestion_date=*" / "data.parquet")
acc_glob = str(src_root / "accounts" / "ingestion_date=*" / "data.parquet")

# Read every raw partition (all-VARCHAR, differing column sets per file). latest_by_key unions
# the columns (diagonal_relaxed) and fills missing as null.
txn_files = sorted(_glob.glob(txn_glob))

if not txn_files:
    # No raw transactions partition (the glob matched nothing). Transactions ACCUMULATE via
    # MERGE, so there is nothing to merge — a no-op leaves existing cleansed.transactions intact.
    # (Do NOT write an empty overwrite: that would wipe accumulated history — A11.)
    print("transactions: no raw partition found — nothing to merge (cleansed left intact)")
    raise SystemExit(0)

# Check the combined raw before anything is derived from it (ADR 0004, df-ahua.3): impossible data
# stops here and cleansed keeps yesterday's rows.
txn_raw = read_raw(txn_files)
validate(txn_raw, "raw.transactions", RAW_TRANSACTIONS_RULES)

# Dedup raw transactions latest-per-id (latest ingested_at wins).
txn_latest = latest_by_key([txn_raw], ["id"], "ingested_at")

acc_files = sorted(_glob.glob(acc_glob))

# Defensive: old raw partitions predate type/paymentData. diagonal_relaxed only surfaces a
# column present in at least one file, so project them as typed NULL if entirely absent — keeps
# reprocess over the full raw history lossless (mirrors raw2cleansed_bills' projection guard).
for _col in ("type", "paymentData", "creditCardMetadata", "status", "operationType",
             "amountInAccountCurrency"):
    if _col not in txn_latest.columns:
        txn_latest = txn_latest.with_columns(pl.lit(None, dtype=pl.Utf8).alias(_col))

# Parse paymentData -> 4 frozen counterparty columns (df-pzu).
txn_latest = txn_latest.with_columns(
    pl.struct(["type", "paymentData"]).map_elements(
        lambda s: _counterparty(s.get("type"), s.get("paymentData")),
        return_dtype=_CP_DTYPE,
    ).alias("_cp")
).unnest("_cp")

# Parse creditCardMetadata -> 4 frozen credit-card columns (fatura-panel spec).
txn_latest = txn_latest.with_columns(
    pl.col("creditCardMetadata").map_elements(_cc_meta, return_dtype=_CC_DTYPE).alias("_cc")
).unnest("_cc")

# df-s65 (A5 freeze) + df-7sa fix: derive description_normalized + etl_merchant_key ONCE here
# (counterparty_name is the already-frozen column from the paymentData unnest above). The
# merchant key cleans a nested-object repr-string via _merchant_name; the raw `merchant`
# column is carried as-is (only the derived key is cleaned).
txn_latest = txn_latest.with_columns(
    pl.struct(["description", "merchant", "counterparty_name"]).map_elements(
        lambda s: _normalize_description(s.get("description")),
        return_dtype=pl.Utf8,
    ).alias("description_normalized"),
    pl.struct(["description", "merchant", "counterparty_name"]).map_elements(
        lambda s: _merchant_key(s.get("description"), s.get("merchant"), s.get("counterparty_name")),
        return_dtype=pl.Utf8,
    ).alias("etl_merchant_key"),
)

# Latest account per id → id + type, joined as account_type.
acc_latest = latest_by_key(
    [pl.read_parquet(f) for f in acc_files], ["id"], "ingested_at"
).select(pl.col("id").alias("accountId"), pl.col("type").alias("account_type"))

new_rows = (
    txn_latest.join(acc_latest, on="accountId", how="left")
    .select(
        "id",
        pl.col("date").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("date"),
        # df-ahua.3.1: a purchase in another currency has the foreign value in `amount` and the
        # account-currency value, converted by the bank, in amountInAccountCurrency (null on every
        # BRL row). cleansed keeps the account-currency amount, the one curated sums.
        pl.coalesce("amountInAccountCurrency", "amount").cast(pl.Float64, strict=False).alias("amount"),
        "category",
        "categoryId",
        "description",
        "descriptionRaw",
        "merchant",
        pl.col("accountName").alias("account"),
        "accountId",
        "account_type",
        "counterparty_name",
        "counterparty_document",
        "counterparty_document_type",
        "payment_method",
        "cc_bill_id",
        "cc_installment_number",
        "cc_total_installments",
        "cc_purchase_date",
        "description_normalized",
        "etl_merchant_key",
        # df-668.1: E4 carries — settled-cash gate + flow direction + operation kind.
        pl.col("status").cast(pl.Utf8).alias("status"),
        pl.col("type").cast(pl.Utf8).alias("txn_type"),
        pl.col("operationType").cast(pl.Utf8).alias("operation_type"),
        "ingested_at",
    )
)

# The rows about to be merged, next to the ingested_at each id already has in cleansed.
previous = (read_delta_table("cleansed", "transactions") if delta_table_exists("cleansed", "transactions")
            else new_rows.clear())
validate(
    new_rows.join(previous.select("id", pl.col("ingested_at").alias("cleansed_ingested_at")),
                  on="id", how="left"),
    "cleansed.transactions", CLEANSED_TRANSACTIONS_RULES,
)

# Accumulate: refreshed ids replace, ids absent from this fetch persist. The Delta MERGE
# (when_matched_update_all + when_not_matched_insert_all) leaves non-matched target rows
# untouched — exactly the accumulate semantic. The schema is fixed by this script.
upsert_delta_table("cleansed", "transactions", new_rows, ["id"])

# Reconciliation (df-ahua.22, df-ahua.4.8): every id of the latest raw is in cleansed once (compared
# with txn_latest, so a row lost on the way to the MERGE is caught too), and every id cleansed had
# before this run is still there (the table accumulates).
written = read_delta_table("cleansed", "transactions")
validate(layer_totals(txn_latest, written, ["id"]), "cleansed.transactions vs raw", IDS_KEPT_RULES)
validate(layer_totals(previous, written, ["id"]), "cleansed.transactions vs last run", ROWS_KEPT_RULES)

print(f"transactions: {written.height} rows written to cleansed")
