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
    anomes = mes.year * 100 + mes.month

    linhas = _ler(workspace / "data" / "curated" / TABELA, lambda tabela: (
        tabela.filter(pl.col("anomes_key") == anomes).select(COLUNAS).sort(["date", "id"], descending=True)))
    return linhas.to_dicts()


def _ler(caminho: Path, consulta) -> pl.DataFrame:
    if not (caminho / "_delta_log").is_dir():
        raise TabelaAusente(str(caminho))
    return _POOL.submit(lambda: consulta(pl.scan_delta(str(caminho))).collect()).result()  # noqa: TID251 -- ninho cannot import dataforge.delta_pool; this pool is its own


def existe(workspace: Path, transacao_id: str) -> bool:
    """Whether the pipeline published a transaction with this id."""
    tabela = workspace / "data" / "curated" / TABELA
    return _ler(tabela, lambda t: t.filter(pl.col("id") == transacao_id).select("id")).height > 0


def versao_aplicada(workspace: Path) -> int | None:
    """The version of the edits the pipeline last classified with; None before its first run."""
    try:
        linhas = _ler(workspace / "data" / "curated" / "edicao_aplicada", lambda t: t.select("versao"))
    except TabelaAusente:
        return None
    return linhas["versao"][0] if linhas.height else None
