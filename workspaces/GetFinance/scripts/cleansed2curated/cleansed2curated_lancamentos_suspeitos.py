# cleansed2curated_lancamentos_suspeitos.py — task `lancamentos_suspeitos` (df-z4fh.2): os lançamentos
# suspeitos. Flat, top-to-bottom. Lê raw/transactions (todas as partições, só leitura), cleansed.transactions
# e curated.conferencia_saldo; grava curated.lancamentos_suspeitos (inteira), uma linha por (lançamento,
# motivo). Só marca: não altera lançamento nenhum e não emite achado suspicious. A regra está em
# report/lancamentos_suspeitos.py.
import argparse
import glob as _glob
import sys
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent.parent / "report"))
from lancamentos_suspeitos import MOTIVOS, lancamentos_suspeitos

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # raw (transactions, every partition)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

# raw/transactions says in which fetches each id came, and its time of day: four columns.
raw_files = sorted(_glob.glob(str(_connection_root(args.source) / "transactions" / "ingestion_date=*"
                                  / "data.parquet")))
raw_transactions = pl.concat([pl.read_parquet(f, columns=["id", "accountId", "date", "ingested_at"])
                              for f in raw_files])
transactions = read_delta_table("cleansed", "transactions")
# The intervals are keyed by account_uuid; dim_account takes them to the Pluggy account the transactions carry.
contas = read_delta_table(args.target, "dim_account").select(
    pl.col("pluggy_account_id").alias("account_id"), "account_uuid")
conferencia = read_delta_table(args.target, "conferencia_saldo").join(contas, on="account_uuid", how="inner")

suspeitos = (
    lancamentos_suspeitos(transactions, raw_transactions, conferencia)
    .join(contas, on="account_id", how="left")
    .select(LANCAMENTOS_SUSPEITOS_COLUMNS)
)

validate(suspeitos, "curated.lancamentos_suspeitos", LANCAMENTOS_SUSPEITOS_RULES, ["date", "motivo"])
write_delta_overwrite(args.target, "lancamentos_suspeitos", suspeitos)

# Counts only: never an id, an amount or an account, since the log may be shared (validate).
por_motivo = dict(suspeitos["motivo"].value_counts().iter_rows())
print(f"lancamentos_suspeitos: {suspeitos['transaction_id'].n_unique()} lançamentos, {suspeitos.height} marcas: "
      + ", ".join(f"{nome} {por_motivo.get(nome, 0)}" for nome, _ in MOTIVOS))
