"""cartao_listas.py — as duas listas do Cartão no Resumo v3: parcelamentos ativos e itens da próxima
fatura. Porta as queries cc_parcelas e cc_fatura_itens do workbook, como estão.

As duas listas são calculadas para um mês só: o último mês do report (maior anomes_key de
report_cartao), como no Excel, em que o seletor de mês não muda essas listas.

Atenção: a série de parcelamento aqui NÃO é a mesma de report_lib._comprometido_parcelas (o número
"comprometido em parcelas"). Lá a série só é anulada por linhas até o mês de corte; aqui por
qualquer linha, como no cc_parcelas. Cada uma está portada como era (spec 2026-11-09 §8).
Funções puras: recebem e devolvem DataFrames, sem I/O.
"""
from datetime import date, timedelta

import polars as pl

from classificacao import TAG_PAGAMENTO_NO_CARTAO
from report_lib import _strip_parcela_suffix

A_VISTA = "à vista"

SCHEMA_PARCELAS = {"anomes_key": pl.Int32, "compra": pl.String, "cartao": pl.String,
                   "categoria": pl.String, "valor_parcela": pl.Float64, "pagas": pl.String,
                   "restam": pl.Int64, "valor_restante": pl.Float64, "comprado_em": pl.Date}
SCHEMA_ITENS = {"anomes_key": pl.Int32, "data": pl.Date, "descricao": pl.String,
                "categoria": pl.String, "parcela": pl.String, "valor": pl.Float64}

_CHAVE_SERIE = ["account_uuid", "_serie", "cc_purchase_date", "cc_total_installments", "_anchor"]


def ultimo_mes(report_cartao: pl.DataFrame) -> int | None:
    """O mês das listas: o maior anomes_key do report do cartão; None sem cartão."""
    if report_cartao.is_empty():
        return None
    return int(report_cartao["anomes_key"].max())


def _fim_do_mes(anomes: int) -> date:
    ano, mes = divmod(anomes, 100)
    no_mes_seguinte = date(ano, mes, 28) + timedelta(days=4)   # dia 28 + 4 sempre cai no mês seguinte
    return no_mes_seguinte.replace(day=1) - timedelta(days=1)


def _indice_mes(col: pl.Expr) -> pl.Expr:
    return (col // 100) * 12 + (col % 100)


def _mes_da_fatura(fatura: pl.DataFrame) -> pl.DataFrame:
    """statement_id -> mês da fatura. Sem o ciclo aberto (statement_id vazio); primeira por id."""
    return (
        fatura.filter(pl.col("statement_id").is_not_null() & (pl.col("statement_id") != ""))
        .select(pl.col("statement_id").alias("cc_bill_id"), pl.col("anomes_key").cast(pl.Int64).alias("_bill_anomes"))
        .unique(subset="cc_bill_id", keep="first", maintain_order=True)
    )


def parcelas_ativas(fact: pl.DataFrame, classificada: pl.DataFrame, fatura: pl.DataFrame,
                    mes: int | None) -> pl.DataFrame:
    """Séries de parcelamento com parcelas a vencer no mês `mes`. `fact` é o curated inteiro,
    COM as linhas anuladas: uma única linha anulada anula a série toda."""
    if mes is None or fact.is_empty():
        return pl.DataFrame(schema=SCHEMA_PARCELAS)
    parcelas = fact.filter(
        (pl.col("account_type") == "CREDIT")
        & (pl.col("cc_total_installments") > 1)
        & pl.col("cc_installment_number").is_not_null()
    ).join(_mes_da_fatura(fatura), on="cc_bill_id", how="left")
    # Anchor = mês da fatura (ou da data) − k: constante na série; separa séries intercaladas da
    # mesma loja com a mesma descrição e sem data de compra.
    mes_data = pl.col("date").dt.year().cast(pl.Int64) * 100 + pl.col("date").dt.month().cast(pl.Int64)
    parcelas = parcelas.with_columns(
        pl.col("description_normalized").map_elements(
            lambda s: _strip_parcela_suffix(s).upper(), return_dtype=pl.String).alias("_serie"),
        (_indice_mes(pl.coalesce(pl.col("_bill_anomes"), mes_data))
         - pl.col("cc_installment_number")).alias("_anchor"),
    )
    anuladas = parcelas.filter(pl.col("netting_applied").fill_null(False)).select(_CHAVE_SERIE).unique()
    observadas = (
        parcelas.filter(~pl.col("netting_applied").fill_null(False))
        .join(anuladas, on=_CHAVE_SERIE, how="anti", nulls_equal=True)
        .filter(pl.col("date").is_not_null() & (pl.col("date") <= _fim_do_mes(mes)))
        # Mais recente primeiro; empate de data fica na ordem original (PQ: Sort desc, {0}).
        .sort("date", descending=True, maintain_order=True)
    )
    series = observadas.group_by(_CHAVE_SERIE, maintain_order=True).agg(
        pl.col("cc_installment_number").max().alias("k"),
        pl.col("amount").first().alias("valor_parcela"),
        pl.col("account_uuid").first().alias("cartao"),
        pl.coalesce(pl.col("cc_purchase_date").first(), pl.col("date").min()).alias("comprado_em"),
        pl.col("description_normalized").first().alias("_desc"),
        pl.col("id").first().alias("_id"),
    ).filter(pl.col("k") < pl.col("cc_total_installments"))
    series = series.join(
        classificada.select(pl.col("id").alias("_id"), "categoria"), on="_id", how="left"
    ).with_columns(
        pl.col("_desc").map_elements(_strip_parcela_suffix, return_dtype=pl.String).alias("compra"),
        (pl.col("k").cast(pl.String) + "/" + pl.col("cc_total_installments").cast(pl.String)).alias("pagas"),
        (pl.col("cc_total_installments") - pl.col("k")).alias("restam"),
        pl.lit(mes).alias("anomes_key"),
    ).with_columns((pl.col("restam") * pl.col("valor_parcela")).alias("valor_restante"))
    return (series.select(list(SCHEMA_PARCELAS)).cast(SCHEMA_PARCELAS)
            .sort("valor_restante", descending=True, maintain_order=True))


def itens_proxima_fatura(classificada: pl.DataFrame, fatura: pl.DataFrame, mes: int | None) -> pl.DataFrame:
    """Itens da próxima fatura com saldo pendente a partir de `mes`. `classificada` já vem sem as
    linhas anuladas (o PQ lia o fact_transaction carregado, que também não as tinha)."""
    if mes is None:
        return pl.DataFrame(schema=SCHEMA_ITENS)
    pendentes = (fatura.filter((pl.col("valor_pendente") > 0) & (pl.col("anomes_key") >= mes))
                 .sort("anomes_key", maintain_order=True))
    if pendentes.is_empty():
        return pl.DataFrame(schema=SCHEMA_ITENS)
    alvo = pendentes.row(0, named=True)
    sid = alvo["statement_id"]
    da_fatura = (pl.col("cc_bill_id") == sid) if sid else (pl.col("cc_bill_id").fill_null("") == "")
    itens = classificada.filter(
        (pl.col("account_type") == "CREDIT")
        & pl.col("etl_tag").ne_missing(TAG_PAGAMENTO_NO_CARTAO)
        & da_fatura
    ).with_columns(
        pl.when(pl.col("cc_installment_number").is_null()).then(pl.lit(A_VISTA))
        .otherwise(pl.col("cc_installment_number").cast(pl.String) + "/"
                   + pl.col("cc_total_installments").cast(pl.String)).alias("parcela"),
        pl.lit(alvo["anomes_key"]).alias("anomes_key"),
    ).select(
        "anomes_key", pl.col("date").alias("data"), pl.col("description_normalized").alias("descricao"),
        "categoria", "parcela", pl.col("amount").alias("valor"),
    )
    return itens.cast(SCHEMA_ITENS).sort("data", maintain_order=True)
