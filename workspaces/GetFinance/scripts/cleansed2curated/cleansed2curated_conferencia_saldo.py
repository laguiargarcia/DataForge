# cleansed2curated_conferencia_saldo.py — task `conferencia_saldo` (df-z4fh.1): a conferência pelo saldo.
# Flat, top-to-bottom. Lê raw/accounts e raw/transactions (todas as partições, só leitura) e
# cleansed.transactions; grava curated.conferencia_saldo (inteira), uma linha por conta BANK e intervalo
# entre dois snapshots. Só mede: não altera lançamento nenhum. A regra está em report/conferencia_saldo.py.
import argparse
import glob as _glob
import sys
from pathlib import Path

import polars as pl

exec(open(Path(__file__).parent.parent / "functions" / "functions.py", encoding="utf-8").read())
sys.path.insert(0, str(Path(__file__).parent.parent / "report"))
from conferencia_saldo import EM_ABERTO, ESTADOS, NAO_FECHA, TOLERANCIA, conferencia_saldo

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)   # raw (accounts and transactions, every partition)
parser.add_argument("--target", required=True)   # curated
args = parser.parse_args()

src_root = _connection_root(args.source)
accounts = read_raw(sorted(_glob.glob(str(src_root / "accounts" / "ingestion_date=*" / "data.parquet"))))
# raw/transactions only gives each transaction its time of day (cleansed keeps the day): three columns.
raw_files = sorted(_glob.glob(str(src_root / "transactions" / "ingestion_date=*" / "data.parquet")))
raw_transactions = pl.concat([pl.read_parquet(f, columns=["id", "date", "ingested_at"]) for f in raw_files])
# The transactions are cleansed's, the set the report sums; dim_account names each account.
transactions = read_delta_table("cleansed", "transactions")
dim_account = read_delta_table(args.target, "dim_account")

conferencia = (
    conferencia_saldo(accounts, transactions, raw_transactions)
    .join(dim_account.select(pl.col("pluggy_account_id").alias("account_id"), "account_uuid"),
          on="account_id", how="left")
    .select(CONFERENCIA_SALDO_COLUMNS)
)

validate(conferencia, "curated.conferencia_saldo", CONFERENCIA_SALDO_RULES + [new_balance_break_rule()],
         "ate")
write_delta_overwrite(args.target, "conferencia_saldo", conferencia)

# Counts, and dates and states of the newest intervals: never an amount or an account, since the log may be
# shared (validate). The amounts are in the table.
por_estado = dict(conferencia["estado"].value_counts().iter_rows())
print(f"conferencia_saldo: {conferencia.height} intervalos (tolerância {TOLERANCIA:.2f}): "
      + ", ".join(f"{e} {por_estado.get(e, 0)}" for e in ESTADOS))
recentes = (conferencia.filter(pl.col("ate").rank("ordinal", descending=True).over("account_uuid") <= BALANCE_WARN_RANK)
            .filter(pl.col("estado").is_in([NAO_FECHA, EM_ABERTO])).sort("ate"))
for r in recentes.iter_rows(named=True):
    print(f"conferencia_saldo: entre os 3 mais novos, {r['de']:%Y-%m-%d %H:%M} -> {r['ate']:%Y-%m-%d %H:%M}"
          f" não fecha ({r['estado']})")
