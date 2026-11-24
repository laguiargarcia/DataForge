# cleansed2curated_veredito_suspeitos.py — task `veredito_suspeitos` (df-z4fh.3): o extrato do banco dá veredito
# a cada lançamento suspeito. Flat, top-to-bottom. Lê raw/transactions (todas as partições, só leitura, para a
# hora), cleansed.transactions, curated.lancamentos_suspeitos, curated.extrato e curated.extrato_saldos; grava
# curated.veredito_suspeitos (uma linha por suspeito e motivo), curated.extrato_sem_lancamento (as linhas do
# extrato sem par) e curated.acerto_por_motivo (inteiras). Sem tabelas de extrato (nenhum PDF lido ainda), todo
# suspeito fica sem_extrato_no_periodo e a task não falha. Só lê e mede: não altera lançamento, fato nem
# relatório, e não emite achado suspicious. A regra está em report/veredito_extrato.py.
import argparse
import glob as _glob
import sys
from pathlib import Path

import polars as pl

exec((Path(__file__).parent.parent / "functions" / "functions.py").read_text(encoding="utf-8"))
sys.path.insert(0, str(Path(__file__).parent.parent / "report"))
from extrato import COLUNAS as COLUNAS_EXTRATO
from extrato import COLUNAS_SALDOS
from lancamentos_suspeitos import MOTIVOS
from veredito_extrato import (FORA_DO_EXTRATO, NO_EXTRATO, TOLERANCIA_DIAS, VEREDITOS, acerto_por_motivo, casar,
                              sem_lancamento, vereditos)

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # raw (transactions, every partition)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# raw/transactions only gives each transaction its time of day (cleansed keeps the day): three columns.
raw_files = sorted(_glob.glob(str(_connection_root(args.source) / "transactions" / "ingestion_date=*"
                                  / "data.parquet")))
raw_transactions = pl.concat([pl.read_parquet(f, columns=["id", "date", "ingested_at"]) for f in raw_files])
transactions = read_delta_table("cleansed", "transactions")
suspeitos = read_delta_table(args.target, "lancamentos_suspeitos")
# The statement tables are keyed by account_uuid; dim_account takes them to the Pluggy account the transactions
# carry. They are missing until the first statement is read: then nothing is covered.
contas = read_delta_table(args.target, "dim_account").select(
    pl.col("pluggy_account_id").alias("account_id"), "account_uuid")
if delta_table_exists(args.target, "extrato"):
    extrato = read_delta_table(args.target, "extrato").join(contas, on="account_uuid", how="inner")
else:
    extrato = pl.DataFrame(schema=COLUNAS_EXTRATO)
if delta_table_exists(args.target, "extrato_saldos"):
    saldos = read_delta_table(args.target, "extrato_saldos").join(contas, on="account_uuid", how="inner")
else:
    saldos = pl.DataFrame(schema=COLUNAS_SALDOS)

casamento = casar(transactions, raw_transactions, extrato, saldos)
por_suspeito = vereditos(casamento, suspeitos).select(VEREDITO_SUSPEITOS_COLUMNS)
sem_par = (sem_lancamento(extrato, casamento).join(contas, on="account_id", how="left")
           .select(EXTRATO_SEM_LANCAMENTO_COLUMNS))
acerto = acerto_por_motivo(por_suspeito, [nome for nome, _ in MOTIVOS]).select(ACERTO_POR_MOTIVO_COLUMNS)

validate(por_suspeito, "curated.veredito_suspeitos", VEREDITO_SUSPEITOS_RULES, ["date", "motivo"])
validate(sem_par, "curated.extrato_sem_lancamento", EXTRATO_SEM_LANCAMENTO_RULES, ["dia", "ordem"])
validate(acerto, "curated.acerto_por_motivo", ACERTO_POR_MOTIVO_RULES, "motivo")
write_delta_overwrite(args.target, "veredito_suspeitos", por_suspeito)
write_delta_overwrite(args.target, "extrato_sem_lancamento", sem_par)
write_delta_overwrite(args.target, "acerto_por_motivo", acerto)

# Counts only: never an id, an amount, a description or an account, since the log may be shared (validate).
por_veredito = dict(por_suspeito.unique("transaction_id")["veredito"].value_counts().iter_rows())
print(f"veredito_suspeitos: {por_suspeito['transaction_id'].n_unique()} lançamentos suspeitos (tolerância "
      f"{TOLERANCIA_DIAS} dias): " + ", ".join(f"{v} {por_veredito.get(v, 0)}" for v in VEREDITOS))
print(f"veredito_suspeitos: extrato: {sem_par.height} linha(s) sem lançamento; "
      f"{casamento.filter(pl.col('veredito') == FORA_DO_EXTRATO).height} lançamento(s) do período coberto sem linha")
for motivo, n, n_no, n_fora in acerto.iter_rows():
    print(f"veredito_suspeitos: {motivo}: {n} suspeito(s), {NO_EXTRATO} {n_no}, {FORA_DO_EXTRATO} {n_fora}")
