# build_report.py — orchestration script for GetFinance report structural aggregates (v3, df-668.3).
# Flat, top-to-bottom. Run: DataForgeRepo/.venv/bin/python3 <this>
from pathlib import Path
import sys
import polars as pl

# --- path resolution (parents[2] = workspace root: GetFinance-Dev or GetFinance) ---
DEV = Path(__file__).resolve().parents[2]
CURATED = DEV / "data" / "curated"
BRIDGE = DEV / "data" / "bridge"
BRIDGE.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))
from report_lib import (
    liquidity_per_month, cartao_per_month, investimentos_per_month, extend_liquidity_v3,
)

# --- load curated Delta tables ---
snap = pl.read_delta(str(CURATED / "fact_balance_snapshot"))
fact = pl.read_delta(str(CURATED / "fact_transaction"))
fatura = pl.read_delta(str(CURATED / "fact_fatura_mes"))
positions = pl.read_delta(str(CURATED / "fact_investment_position"))

# --- compute the three structural aggregates (spec §11 E5/E6) ---
cartao = cartao_per_month(snap, fatura, fact)
invest = investimentos_per_month(fact, positions)
liq = extend_liquidity_v3(liquidity_per_month(snap, fact), cartao, invest)

# --- write outputs ---
liq.write_csv(BRIDGE / "report_liquidity.csv")
cartao.write_csv(BRIDGE / "report_cartao.csv")
invest.write_csv(BRIDGE / "report_investimentos.csv")

print(f"report_liquidity.csv: {len(liq)} rows | report_cartao.csv: {len(cartao)} rows "
      f"| report_investimentos.csv: {len(invest)} rows -> {BRIDGE}")

jun = liq.filter(pl.col("anomes_key") == liq["anomes_key"].max())
if len(jun) == 1:
    r = jun.row(0, named=True)
    pat = f"{r['patrimonio']:.2f}" if r["patrimonio"] is not None else "null (no position snapshot)"
    print(
        f"{r['mes_ano']} sanity: caixa_saldo={r['caixa_saldo']:.2f} "
        f"proxima_fatura={r['proxima_fatura']:.2f} disponivel_gastar={r['disponivel_gastar']:.2f} "
        f"patrimonio={pat}"
    )
