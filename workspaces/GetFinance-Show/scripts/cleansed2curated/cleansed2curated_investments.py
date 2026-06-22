import argparse
import os
import yaml
import polars as pl
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed
parser.add_argument("--target", required=True)   # curated
parser.add_argument("--investments-config", default=None)
args = parser.parse_args()

# investments.yaml owns the holding_uuid surrogate map (pluggy_holding_id -> holding_uuid).
# (df-6j6: accounts.yaml is deleted; the itemId -> institution map is now DERIVED from the
# account layers below, not from an accounts.yaml `institutions:` block. Investments natural-key
# derivation itself stays deferred to df-rir — this is the minimal change to survive the delete.)
investments_config_path = Path(
    args.investments_config
    or os.environ.get("INVESTMENTS_CONFIG")
    or str(Path(__file__).parent.parent.parent / "config" / "investments.yaml")
)

inv_cfg = yaml.safe_load(investments_config_path.read_text(encoding="utf-8")) or {}
holdings_map = inv_cfg.get("holdings") or {}          # pluggy_holding_id -> {holding_uuid, alias}

# itemId -> institution, derived (no accounts.yaml): cleansed.accounts carries pluggy_account_id
# + itemId; curated.dim_account carries pluggy_account_id + the derived institution. Joining them
# yields itemId -> institution for every connection with a bank/credit account (one item = one
# institution). One row per itemId (the institution is shared across the item's accounts).
# DATA DEP (df-6j6): this now requires curated.dim_account. In finance_daily the dim_accounts task
# is transitively upstream (dim_accounts -> fact_balance -> ... -> transform_investments ->
# dim_investments), so ordering holds; this guard makes a standalone/out-of-order run fail clearly.
if not delta_table_exists(args.target, "dim_account"):
    raise SystemExit(
        "dim_investment requires curated.dim_account (itemId -> institution is derived from it). "
        "Run the dim_accounts task first; finance_daily guarantees this ordering."
    )
_acc = read_delta_table(args.source, "accounts").select("id", "itemId")
_dim_acc = read_delta_table(args.target, "dim_account").select("pluggy_account_id", "institution")
institution_map = (
    _acc.join(_dim_acc, left_on="id", right_on="pluggy_account_id", how="inner")
    .select("itemId", "institution")
    .unique(subset=["itemId"], keep="first")
)
holding_map = pl.DataFrame(
    {
        "pluggy_holding_id": list(holdings_map.keys()),
        "holding_uuid": [m["holding_uuid"] for m in holdings_map.values()],
        "alias": [m.get("alias") for m in holdings_map.values()],
    },
    schema={"pluggy_holding_id": pl.Utf8, "holding_uuid": pl.Utf8, "alias": pl.Utf8},
)

investments = read_delta_table(args.source, "investments")

# dim_investment (spec §5, grain holding_uuid): only holdings present in investments.yaml
# (mapped). The surrogate holding_uuid comes FROM that map (JOIN) — a user-controlled slug like
# "bank-cdb-2099-01" (NOT a hash of the Pluggy id), so on reconnect the new pluggy_holding_id is
# repointed to the same holding_uuid and snapshot history does not split (A1). pluggy_holding_id
# is the current-link column. name = COALESCE(alias, raw Pluggy name) (mirrors U3): when several
# holdings share one raw Pluggy name (e.g. multiple CDBs from the same bank), alias distinguishes them.
# institution is resolved via the holding's itemId against the derived item->institution map
# (supplies it for FIXED_INCOME holdings where Pluggy leaves it NULL). subtype is carried through (A19): all live
# holdings are type=FIXED_INCOME, so subtype (CDB vs TREASURY) is the sole discriminator of
# investment kind. Descriptive-only: measures (balance/amountOriginal) and currency stay in U7's
# fact (A18; spec §5 omits both). Safe to drop/rebuild from the accumulating cleansed layer each
# run. INNER JOIN on holding_map excludes unmapped holdings (quarantined below).
# Drop cleansed's own `institution` column before the institution_map join: the dim's
# institution is the config-resolved name (SQL used i.institution), and for FIXED_INCOME the
# Pluggy-supplied cleansed institution is NULL anyway. Dropping it also avoids a join-suffix clash.
dim = (
    investments.drop("institution")
    .join(holding_map, left_on="id", right_on="pluggy_holding_id", how="inner")
    .join(institution_map, on="itemId", how="left")
    .select(
        "holding_uuid",
        pl.col("id").alias("pluggy_holding_id"),
        pl.coalesce([pl.col("alias"), pl.col("name")]).alias("name"),
        "type",
        "subtype",
        "issuer",
        "institution",
    )
)

# Fail loud if a MAPPED holding's itemId resolves to a NULL/blank institution — a half-resolved
# identity would silently mis-scope downstream (A1: never write a half-resolved identity; A10:
# institutions is the authoritative known-set). Placed before the write so dim_investment is
# never left half-built. (Unmapped holdings are excluded by the holding_map JOIN and quarantined;
# this guard catches mapped holdings whose itemId is missing/blank in institutions:.)
missing_df = dim.filter(
    pl.col("institution").is_null()
    | (pl.col("institution").str.strip_chars() == "")
)
if len(missing_df) > 0:
    missing = list(zip(missing_df["pluggy_holding_id"].to_list(),
                       missing_df["holding_uuid"].to_list()))
    raise SystemExit(
        f"dim_investment: {len(missing)} mapped holding(s) have an itemId that resolves to no "
        f"institution via the account layers — {missing}. Ensure the holding's Pluggy item has a "
        f"bank/credit account in dim_account (so its institution is derivable) and re-run."
    )

write_delta_overwrite(args.target, "dim_investment", dim)

# Quarantine: cleansed holdings with no mapping entry in investments.yaml — surfaced for manual
# repoint/onboarding (add the pluggy_holding_id under holdings:), never auto-resolved (A1).
# Written back to the cleansed db, mirroring U3's accounts_unmapped (holding-grain LEFT-JOIN-NULL
# on the natural key). This realizes the investments quarantine table A1 deferred to U5–U7.
unmapped = (
    investments.join(holding_map, left_on="id", right_on="pluggy_holding_id", how="anti")
    .select(
        pl.col("id").alias("pluggy_holding_id"),
        "itemId",
        "type",
        "name",
        "ingested_at",
    )
)
write_delta_overwrite(args.source, "investments_unmapped", unmapped)

print(f"dim_investment: {len(dim)} rows -> curated ({len(unmapped)} unmapped quarantined)")
