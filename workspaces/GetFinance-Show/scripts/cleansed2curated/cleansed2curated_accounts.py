import argparse
import ast
import os
import re
import unicodedata
import yaml
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed
parser.add_argument("--target", required=True)   # curated
parser.add_argument("--institutions-config", default=None)
args = parser.parse_args()

# D1: bank (COMPE) code -> institution display name. Permanent key; set once per bank.
config_path = Path(
    args.institutions_config
    or os.environ.get("INSTITUTIONS_CONFIG")
    or str(Path(__file__).parent.parent.parent / "config" / "institutions.yaml")
)
# encoding pinned: the daily job runs under Windows Python (cp1252 locale default), which would
# mis-decode UTF-8 names like 'Itaú' (bytes c3 ba) into 'ItaÃº' → slug 'itaao'. Always read utf-8.
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
bank_names = cfg.get("banks") or {}                 # "341" -> "Itaú"


def _slugify(institution):
    """institution-slug: ASCII-fold (strip diacritics), lowercase, non-alnum runs -> single '-'
    (e.g. 'Itaú' -> 'itau', 'C6 Bank' -> 'c6-bank'). Trailing/leading '-' stripped."""
    folded = unicodedata.normalize("NFKD", institution or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def _last4(number):
    """last4: last 4 digits of `number` after stripping non-digits ('00012345-2' -> '3452')."""
    digits = re.sub(r"\D", "", str(number or ""))
    return digits[-4:] if digits else ""


def _account_segment(transfer_number):
    """Account-number segment of transferNumber (its last '/'-part) — used as the last4 source
    when a BANK account has no `number` ('341/1620/00012345-2' -> '00012345-2')."""
    if not transfer_number:
        return None
    return str(transfer_number).split("/")[-1].strip()


def _kind(account_type, subtype):
    """checking / savings (from subtype) for BANK; credit for CREDIT."""
    if account_type == "CREDIT":
        return "credit"
    sub = (subtype or "").upper()
    if "SAVING" in sub:
        return "savings"
    return "checking"


def _bank_code(transfer_number):
    """bank (COMPE) code = first '/'-segment of transferNumber ('341/1620/00012345-2' -> '341').
    COMPE codes are always exactly 3 digits; some connectors instead return only 'agency/account'
    (no bank code — see getfinance-pluggy-integration.md §4), where the first segment is the agency.
    Return None unless the first segment is a 3-digit code, so those fall to the plain `unknown`
    placeholder rather than being mis-keyed as `unknown-{agency}`."""
    if not transfer_number:
        return None
    first = str(transfer_number).split("/")[0].strip()
    return first if re.fullmatch(r"\d{3}", first) else None


def _credit_level(credit_data):
    """creditData.level title-cased (repr-parsed), e.g. 'PLATINUM' -> 'Platinum'. None if absent."""
    if not credit_data:
        return None
    try:
        cd = ast.literal_eval(str(credit_data))
        level = cd.get("level")
        return level.title() if level else None
    except (ValueError, SyntaxError, AttributeError):
        return None


accounts = read_delta_table(args.source, "accounts").to_dicts()

# Pass 1 (BANK rows): bank_code from transfer_number -> institution via institutions.yaml,
# else a logged `unknown-{code}` placeholder. Build itemId -> institution for the item.
item_institution = {}
for a in accounts:
    if a["type"] != "BANK":
        continue
    code = _bank_code(a.get("transfer_number"))
    if code and code in bank_names:
        institution = bank_names[code]
    else:
        institution = f"unknown-{code}" if code else "unknown"
        print(f"dim_account: BANK account {a['id']} has bank code {code!r} not in "
              f"institutions.yaml — using placeholder {institution!r} (account still flows).")
    item_institution.setdefault(a["itemId"], institution)

# Pass 2 (CREDIT / no transfer_number): institution = the item's bank institution from Pass 1;
# else a logged `unknown` placeholder (no current case — every connection today has a bank account).
rows = []
for a in accounts:
    account_type = a["type"]
    if account_type == "BANK":
        code = _bank_code(a.get("transfer_number"))
        institution = bank_names.get(code) if code and code in bank_names else (
            f"unknown-{code}" if code else "unknown")
    else:
        institution = item_institution.get(a["itemId"])
        if institution is None:
            institution = "unknown"
            print(f"dim_account: CREDIT account {a['id']} (item {a['itemId']}) has no BANK "
                  f"account to inherit an institution from — using placeholder 'unknown'.")

    kind = _kind(account_type, a.get("subtype"))
    # Prefer the account's `number`; fall back to the transferNumber account segment for a BANK
    # account that arrives without a `number` (keeps the uuid stable instead of degenerate).
    last4 = _last4(a.get("number") or _account_segment(a.get("transfer_number")))
    slug = _slugify(institution)
    account_uuid = f"{slug}-{kind}-{last4}"

    # alias (report-visible). BANK -> '{institution} Conta Corrente' (checking) / 'Poupança'
    # (savings). CREDIT -> '{institution} Cartão {Level}' (creditData.level title-cased).
    if account_type == "BANK":
        suffix = "Poupança" if kind == "savings" else "Conta Corrente"
        alias = f"{institution} {suffix}"
    else:
        level = _credit_level(a.get("creditData"))
        alias = f"{institution} Cartão {level}" if level else f"{institution} Cartão"

    rows.append({
        "account_uuid": account_uuid,
        "pluggy_account_id": a["id"],
        "name": alias,
        "institution": institution,
        "type": account_type,
        "currency": a["currency"],
    })

# dim_account: same column set as before (account_uuid, pluggy_account_id, name=alias,
# institution, type, currency). NO filter — every account flows (D3). Safe to drop/rebuild
# from the accumulating cleansed layer each run.
dim = pl.DataFrame(
    rows,
    schema={
        "account_uuid": pl.Utf8,
        "pluggy_account_id": pl.Utf8,
        "name": pl.Utf8,
        "institution": pl.Utf8,
        "type": pl.Utf8,
        "currency": pl.Utf8,
    },
)

write_delta_overwrite(args.target, "dim_account", dim)

print(f"dim_account: {len(dim)} rows -> curated")
