# cleansed2curated_conferencia_fatura.py — task `conferencia_fatura` (df-acz4): a conferência do cartão pela fatura.
# Flat, top-to-bottom. Lê curated.fact_card_statement e curated.dim_account, e cleansed.bills e
# cleansed.transactions; grava curated.conferencia_fatura (inteira), uma linha por fatura fechada. Só mede: não
# altera lançamento nenhum. A regra está em report/conferencia_fatura.py.
import argparse
import sys
from pathlib import Path

import polars as pl

exec((Path(__file__).parent.parent / "functions" / "functions.py").read_text(encoding="utf-8"))
sys.path.insert(0, str(Path(__file__).parent.parent / "report"))
from conferencia_fatura import ESTADOS_LANCAMENTOS, ESTADOS_PAGAMENTO, TOLERANCIA, conferencia_fatura

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # cleansed (bills and transactions)
parser.add_argument("--target", required=True)   # curated (fact_card_statement, dim_account; written here)
args = parser.parse_args()

faturas = read_delta_table(args.target, "fact_card_statement")
contas = read_delta_table(args.target, "dim_account").select(pl.col("pluggy_account_id").alias("accountId"),
                                                             "account_uuid")
# Every bill id, the re-issued twins fact_card_statement left out included: a charge may still point to one.
bills = (read_delta_table(args.source, "bills").join(contas, on="accountId", how="inner")
         .select("id", "account_uuid",
                 pl.col("dueDate").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("due_date")))
lancamentos = (read_delta_table(args.source, "transactions").filter(pl.col("account_type") == "CREDIT")
               .join(contas, on="accountId", how="inner")
               .select("account_uuid", "date", "amount", "categoryId", "cc_bill_id"))

conferencia = conferencia_fatura(faturas, bills, lancamentos, run_day())

validate(conferencia, "curated.conferencia_fatura", CONFERENCIA_FATURA_RULES, "statement_id")
validate(layer_totals(faturas.select("statement_id"), conferencia, ["statement_id"]),
         "curated.conferencia_fatura vs fact_card_statement", ROWS_CARRIED_RULES, "statement_id")
write_delta_overwrite(args.target, "conferencia_fatura", conferencia)

# Counts only: never an amount or an account, since the log may be shared (validate). The amounts are in the table.
for coluna, estados in (("estado_lancamentos", ESTADOS_LANCAMENTOS), ("estado_pagamento", ESTADOS_PAGAMENTO)):
    por_estado = dict(conferencia[coluna].value_counts().iter_rows())
    print(f"conferencia_fatura: {conferencia.height} faturas, {coluna.removeprefix('estado_')} "
          f"(tolerância {TOLERANCIA:.2f}): " + ", ".join(f"{e} {por_estado.get(e, 0)}" for e in estados))
