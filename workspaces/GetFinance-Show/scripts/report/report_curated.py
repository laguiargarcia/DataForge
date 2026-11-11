# report_curated.py — task `report` (df-cns etapa 1): os números e listas do Resumo v3, gravados no
# curated para o app ler. Flat, top-to-bottom. Cada tabela é derivada e regravada inteira.
# Lê: fact_transaction, fact_transaction_classified (task classify), fact_fatura_mes,
#     fact_balance_snapshot, fact_investment_position.
import argparse
import sys
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent))
from cartao_listas import itens_proxima_fatura, parcelas_ativas, ultimo_mes
from report_lib import (
    cartao_per_month, extend_liquidity_v3, investimentos_per_month, liquidity_per_month,
)
from saude import saude_por_categoria, saude_por_mes

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # curated
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

fact = read_delta_table(args.source, "fact_transaction")
classificada = read_delta_table(args.source, "fact_transaction_classified")
fatura = read_delta_table(args.source, "fact_fatura_mes")
snap = read_delta_table(args.source, "fact_balance_snapshot")
positions = read_delta_table(args.source, "fact_investment_position")

# Cartão, investimentos e liquidez (já eram ETL, rodados à mão pelo build_report.py).
cartao = cartao_per_month(snap, fatura, fact)
invest = investimentos_per_month(fact, positions)
liquidez = extend_liquidity_v3(liquidity_per_month(snap, fact), cartao, invest)

# O que antes só existia no workbook.
mes = ultimo_mes(cartao)
saidas = {
    "report_saude": saude_por_mes(classificada),
    "report_saude_categoria": saude_por_categoria(classificada),
    "report_parcelas": parcelas_ativas(fact, classificada, fatura, mes),
    "report_fatura_itens": itens_proxima_fatura(classificada, fatura, mes),
    "report_liquidity": liquidez,
    "report_cartao": cartao,
    "report_investimentos": invest,
}
for tabela, df in saidas.items():
    write_delta_overwrite(args.target, tabela, df)

print(" | ".join(f"{t}: {df.height}" for t, df in saidas.items()) + f" | último mês: {mes}")
