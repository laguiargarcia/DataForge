"""lancamentos_suspeitos.py — lançamentos suspeitos (df-z4fh.2): cada lançamento de cleansed.transactions que
um motivo marca, com o intervalo da conferência pelo saldo (conferencia_saldo.py) em que ele cai.

Os motivos vêm do que a df-7twv mediu na conta corrente contra o saldo e o extrato do banco: o Pluggy larga
lançamentos reais, inventa linhas de aplicação/resgate automático e deixa pendentes duplicados. Cada motivo
é uma expressão com nome em MOTIVOS, avaliada sobre uma linha por lançamento com estas colunas:
- status, description: as de cleansed (a versão da coleta mais nova do id);
- n_coletas: em quantas coletas (dias de raw/transactions) o id veio;
- na_coleta_mais_nova: o id veio na coleta mais nova da sua conta;
- na_janela: o dia do lançamento está entre o primeiro e o último dia que a coleta mais nova da conta
  devolve (a janela é comparada por dia: cleansed guarda só o dia).
Um motivo novo é uma linha a mais em MOTIVOS. Um lançamento pode ter mais de um motivo: sai uma linha por
(lançamento, motivo).

O intervalo é o da conferência com de < instante <= ate, o instante vindo de raw (conferencia_saldo.instantes).
Um lançamento fora de todo intervalo (cartão, antes do primeiro snapshot, depois do último) fica sem.

Só marca: ser suspeito não muda nada no dado nem no relatório (df-z4fh). Funções puras, sem I/O.
"""
import polars as pl

from classificacao import eh_aplicacao_automatica
from conferencia_saldo import instantes

_SUMIU = pl.col("na_janela") & ~pl.col("na_coleta_mais_nova")

MOTIVOS = [
    ("sumiu_da_coleta", _SUMIU),
    ("pendente_que_sumiu", _SUMIU & (pl.col("status") == "PENDING")),
    ("aplicacao_automatica", eh_aplicacao_automatica()),
    ("uma_coleta_so", (pl.col("n_coletas") == 1) & ~pl.col("na_coleta_mais_nova")),
]

COLUNAS = {
    "transaction_id": pl.String,
    "account_id": pl.String,
    "date": pl.Date,
    "amount": pl.Float64,
    "description": pl.String,
    "status": pl.String,
    "motivo": pl.String,
    "primeira_coleta": pl.Date,
    "ultima_coleta": pl.Date,
    "n_coletas": pl.Int64,
    "intervalo_de": pl.Datetime("us", "UTC"),
    "intervalo_ate": pl.Datetime("us", "UTC"),
    "intervalo_estado": pl.String,
}


def _dia(col: str) -> pl.Expr:
    return pl.col(col).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False)


def _coletas(transactions: pl.DataFrame, raw_transactions: pl.DataFrame) -> pl.DataFrame:
    # Por lançamento: primeira_coleta, ultima_coleta, n_coletas, na_coleta_mais_nova e na_janela.
    raw = raw_transactions.select(
        "id", "accountId", _dia("ingested_at").alias("coleta"), _dia("date").alias("dia"))
    por_id = raw.group_by("id").agg(
        pl.col("coleta").min().alias("primeira_coleta"),
        pl.col("coleta").max().alias("ultima_coleta"),
        pl.col("coleta").n_unique().cast(pl.Int64).alias("n_coletas"),
    )
    mais_nova = (
        raw.filter(pl.col("coleta") == pl.col("coleta").max().over("accountId"))
        .group_by("accountId")
        .agg(pl.col("coleta").first().alias("coleta_mais_nova"),
             pl.col("dia").min().alias("janela_de"), pl.col("dia").max().alias("janela_ate"))
    )
    return (
        transactions.join(por_id, on="id", how="left")
        .join(mais_nova, on="accountId", how="left")
        .with_columns(
            pl.col("n_coletas").fill_null(0),
            (pl.col("ultima_coleta") == pl.col("coleta_mais_nova")).fill_null(False).alias("na_coleta_mais_nova"),
            pl.col("date").is_between(pl.col("janela_de"), pl.col("janela_ate")).fill_null(False)
            .alias("na_janela"),
        )
    )


def _intervalos(transactions: pl.DataFrame, raw_transactions: pl.DataFrame,
                conferencia: pl.DataFrame) -> pl.DataFrame:
    # id -> intervalo_de, intervalo_ate, intervalo_estado: o intervalo com de < instante <= ate. O join_asof
    # acha o primeiro ate >= instante da conta; o instante no primeiro snapshot ou antes dele não tem de <
    # instante e fica sem intervalo, como o que vem depois do último.
    tipos = {"de": COLUNAS["intervalo_de"], "ate": COLUNAS["intervalo_ate"], "estado": pl.String}
    intervalos = conferencia.select("account_id", *tipos).cast(tipos).sort("ate")
    return (
        instantes(transactions, raw_transactions)
        .select("id", "account_id", "instante")
        .sort("instante")
        .join_asof(intervalos, left_on="instante", right_on="ate", by="account_id", strategy="forward",
                   check_sortedness=False)
        .filter(pl.col("de") < pl.col("instante"))
        .select("id", pl.col("de").alias("intervalo_de"), pl.col("ate").alias("intervalo_ate"),
                pl.col("estado").alias("intervalo_estado"))
    )


def lancamentos_suspeitos(transactions: pl.DataFrame, raw_transactions: pl.DataFrame,
                          conferencia: pl.DataFrame) -> pl.DataFrame:
    """Uma linha por (lançamento, motivo), com as coletas do id e o intervalo da conferência em que cai.

    transactions: cleansed.transactions (id, accountId, date, amount, description, status); `date` é o dia.
    raw_transactions: raw/transactions de todas as partições (id, accountId, date, ingested_at; texto).
    conferencia: os intervalos de curated.conferencia_saldo (account_id, de, ate, estado).
    """
    lancamentos = _coletas(transactions, raw_transactions)
    marcados = pl.concat([
        lancamentos.filter(regra.fill_null(False)).with_columns(pl.lit(nome).alias("motivo"))
        for nome, regra in MOTIVOS
    ])
    return (
        marcados.join(_intervalos(transactions, raw_transactions, conferencia), on="id", how="left")
        .rename({"id": "transaction_id", "accountId": "account_id"})
        .select(list(COLUNAS))
        .cast(COLUNAS)
        .sort("date", "transaction_id", "motivo")
    )
