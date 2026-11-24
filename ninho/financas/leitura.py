"""Reading the finance tables the pipeline publishes, read-only (docs/architecture-v2/published-tables.md).

The server never writes to the lake and reads only published tables, straight from their Delta files
under ``<workspace>/data/curated``. Every read runs on a pool whose threads live as long as the
process: on Windows a Delta read on a thread that then ends crashes the process tens of reads later
(df-5lk8), and the server's request threads end when idle.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import polars as pl

TABELA = "fact_transaction_classified"
# The columns the route hands out, as the pipeline names them: what a screen needs to show a
# transaction and correct its category. The declared schema of the published tables is still to be
# written (published-tables.md); a column missing here fails the read instead of answering less.
COLUNAS = (
    "id", "date", "anomes_key", "description_normalized", "amount_signed", "account_type",
    "categoria", "subcategoria", "corrigida",
)

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ninho-delta")


class TabelaAusente(LookupError):
    """The published table does not exist yet: the pipeline has not run in this workspace."""


def transacoes_do_mes(workspace: Path, mes: date) -> list[dict]:
    """The transactions of the month of ``mes`` (its ``anomes_key``), newest first."""
    caminho = workspace / "data" / "curated" / TABELA
    if not (caminho / "_delta_log").is_dir():
        raise TabelaAusente(str(caminho))
    anomes = mes.year * 100 + mes.month

    def ler() -> pl.DataFrame:
        return (
            pl.scan_delta(str(caminho))  # noqa: TID251 -- ninho cannot import dataforge.delta_pool; this pool is its own
            .filter(pl.col("anomes_key") == anomes)
            .select(COLUNAS)
            .sort(["date", "id"], descending=True)
            .collect()
        )

    return _POOL.submit(ler).result().to_dicts()
