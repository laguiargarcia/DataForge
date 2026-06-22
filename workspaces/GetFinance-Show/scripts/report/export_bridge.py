# Bridge export: this workspace's curated Delta -> CSVs the report workbook reads. Flat, top-to-bottom.
# Workspace-relative (parents[2] = workspace root), so it runs correctly in BOTH GetFinance-Dev and
# GetFinance (prod): reads <ws>/data/curated, writes <ws>/data/bridge. The workbook PQ resolves the
# bridge relative to its OWN folder (WbFolder), so the same workbook reads the local bridge per workspace.
# routing key is added in Power Query. Run: DataForgeRepo/.venv/bin/python <this>
from pathlib import Path
import polars as pl

DEV = Path(__file__).resolve().parents[2]  # workspace root (GetFinance-Dev or GetFinance)
CURATED = DEV / "data" / "curated"
BRIDGE = DEV / "data" / "bridge"
BRIDGE.mkdir(parents=True, exist_ok=True)

# --- fact_transaction (raw enriched + anomes_key; routing key added later in Power Query) ---
fact = pl.read_delta(str(CURATED / "fact_transaction")).with_columns(
    (pl.col("date").dt.year() * 100 + pl.col("date").dt.month()).cast(pl.Int32).alias("anomes_key")
)
fact.write_csv(BRIDGE / "fact_transaction.csv")

# --- dim_calendar: DAILY grain over the FULL span across ALL dated facts, so every fact date
# (incl. snapshot/investment days beyond the last transaction) gets a calendar row. Spanning only
# fact[date] left same-day snapshot/investment rows orphaned in a NULL month member (df-9fc). ---
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_snap_d = pl.read_delta(str(CURATED / "fact_balance_snapshot")).get_column("snapshot_date")
_inv_d = pl.read_delta(str(CURATED / "fact_investment_position")).get_column("snapshot_date")
# _fat_d uses mes_inicio: the open fatura row lands on day 1 of a possibly-FUTURE month,
# so it can extend cal_max forward — without it that row orphans into a NULL month (df-9fc).
_fat_d = pl.read_delta(str(CURATED / "fact_fatura_mes")).get_column("mes_inicio")
cal_min = min(fact["date"].min(), _snap_d.min(), _inv_d.min(), _fat_d.min())
cal_max = max(fact["date"].max(), _snap_d.max(), _inv_d.max(), _fat_d.max())
days = pl.date_range(cal_min, cal_max, interval="1d", eager=True)
cal = (
    pl.DataFrame({"date": days})
    .with_columns(
        (pl.col("date").dt.year() * 100 + pl.col("date").dt.month()).cast(pl.Int32).alias("anomes_key"),
        pl.col("date").dt.year().alias("ano"),
        pl.col("date").dt.month().alias("mes"),
    )
    .with_columns(pl.col("mes").map_elements(lambda m: MESES[m - 1], return_dtype=pl.String).alias("mes_nome"))
    .with_columns((pl.col("mes_nome") + "/" + pl.col("ano").cast(pl.String)).alias("mes_ano"))
)
cal.write_csv(BRIDGE / "dim_calendar.csv")

# --- supporting dim/fact tables consumed by the workbook (curated -> CSV, columns unchanged) ---
SUPPORTING = ["dim_account", "dim_investment", "dim_counterparty", "fact_balance_snapshot",
              "fact_investment_position", "fact_fatura_mes"]
for tbl in SUPPORTING:
    pl.read_delta(str(CURATED / tbl)).write_csv(BRIDGE / f"{tbl}.csv")

print("fact rows:", fact.height, "| anomes:", fact["anomes_key"].min(), "->", fact["anomes_key"].max())
print("calendar days:", cal.height, "| months:", cal["anomes_key"].n_unique())
print("supporting:", ", ".join(SUPPORTING))
print("wrote bridge ->", BRIDGE)
