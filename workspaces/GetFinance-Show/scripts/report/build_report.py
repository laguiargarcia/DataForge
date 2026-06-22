# build_report.py — orchestration script for GetFinance report v2 liquidity table.
# Flat, top-to-bottom. Run: DataForgeRepo/.venv/bin/python3 <this>
from pathlib import Path
import sys
import polars as pl

# --- path resolution ---
# __file__ = .../GetFinance-Dev/scripts/report/build_report.py
# parents[0] = .../scripts/report
# parents[1] = .../scripts
# parents[2] = .../GetFinance-Dev  (workspace root)
DEV = Path(__file__).resolve().parents[2]
CURATED = DEV / "data" / "curated"
BRIDGE = DEV / "data" / "bridge"
BRIDGE.mkdir(parents=True, exist_ok=True)

# add report_lib dir to sys.path so we can import it
sys.path.insert(0, str(Path(__file__).parent))
from report_lib import liquidity_per_month  # noqa: E402

# --- load curated Delta tables ---
snap = pl.read_delta(str(CURATED / "fact_balance_snapshot"))
fact = pl.read_delta(str(CURATED / "fact_transaction"))

# --- compute liquidity per month ---
liq = liquidity_per_month(snap, fact)

# --- write output ---
out_path = BRIDGE / "report_liquidity.csv"
liq.write_csv(out_path)

# --- print summary ---
print(f"report_liquidity.csv: {len(liq)} rows -> {out_path}")

jun = liq.filter(pl.col("anomes_key") == 202606)
if len(jun) == 1:
    r = jun.row(0, named=True)
    print(
        f"jun/2026 sanity: card_outstanding={r['card_outstanding']:.2f} "
        f"cash_disponivel={r['cash_disponivel']:.2f} "
        f"saldo_disponivel={r['saldo_disponivel']:.2f}"
    )
else:
    print("jun/2026: no row found")
