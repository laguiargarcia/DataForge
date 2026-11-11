"""saude.py — Saúde do mês (base caixa) e detalhamento por categoria, sobre a transação classificada.

Porta as fórmulas do Resumo v3 (Resumo!C6:C11 e os detalhamentos R6/U6/X6/AA6). Só entram linhas
cash_flag (E4: dinheiro que passou pela conta, liquidado).
- Entradas: lado "entrada" com amount_signed > 0.
- Cada saída: −Σ amount_signed do Tipo, sem filtro de sinal (um estorno reduz a saída).
Funções puras: recebem e devolvem DataFrames, sem I/O.
"""
import polars as pl

from classificacao import TIPOS_SAIDA
from report_lib import MESES

GRUPO_ENTRADAS = "Entradas"
# Tipo de saída -> coluna em saude_por_mes, na ordem de TIPOS_SAIDA. O nome do grupo é o Tipo.
_SAIDAS = dict(zip(TIPOS_SAIDA, ("saidas_essencial", "saidas_nao_essencial", "saidas_pagamento_cartao"),
                   strict=True))

_SCHEMA_MES = {"anomes_key": pl.Int32, "mes_ano": pl.String, "entradas": pl.Float64,
               **{col: pl.Float64 for col in _SAIDAS.values()},
               "saidas_total": pl.Float64, "sobra": pl.Float64}
_SCHEMA_CATEGORIA = {"anomes_key": pl.Int32, "grupo": pl.String, "categoria": pl.String, "valor": pl.Float64}

_E_ENTRADA = (pl.col("lado") == "entrada") & (pl.col("amount_signed") > 0)


def _mes_ano(anomes: pl.Expr) -> pl.Expr:
    nome = (anomes % 100).replace_strict(dict(enumerate(MESES, start=1)), return_dtype=pl.String)
    return nome + "/" + (anomes // 100).cast(pl.String)


def saude_por_mes(classificada: pl.DataFrame) -> pl.DataFrame:
    """Uma linha por mês com linhas de caixa: entradas, as três saídas, total e sobra."""
    caixa = classificada.filter(pl.col("cash_flag"))
    if caixa.is_empty():
        return pl.DataFrame(schema=_SCHEMA_MES)
    df = caixa.group_by("anomes_key").agg(
        pl.col("amount_signed").filter(_E_ENTRADA).sum().alias("entradas"),
        *[(-pl.col("amount_signed").filter(pl.col("Tipo") == tipo).sum()).alias(col)
          for tipo, col in _SAIDAS.items()],
    ).with_columns(pl.col(pl.Float64).fill_null(0.0))
    # −Σ(vazio) dá −0.0, que a tela formataria como "-R$ 0,00": normaliza para 0.0.
    df = df.with_columns(
        pl.when(pl.col(c) == 0).then(0.0).otherwise(pl.col(c)).alias(c) for c in _SAIDAS.values()
    )
    df = df.with_columns(pl.sum_horizontal(list(_SAIDAS.values())).alias("saidas_total"))
    df = df.with_columns(
        (pl.col("entradas") - pl.col("saidas_total")).alias("sobra"),
        pl.col("anomes_key").cast(pl.Int32),
        _mes_ano(pl.col("anomes_key")).alias("mes_ano"),
    )
    return df.select(list(_SCHEMA_MES)).cast(_SCHEMA_MES).sort("anomes_key")


def saude_por_categoria(classificada: pl.DataFrame) -> pl.DataFrame:
    """mês × grupo × categoria -> valor positivo. Mesmo filtro da linha correspondente da Saúde."""
    caixa = classificada.filter(pl.col("cash_flag"))
    grupo = pl.when(_E_ENTRADA).then(pl.lit(GRUPO_ENTRADAS))
    for tipo in _SAIDAS:
        grupo = grupo.when(pl.col("Tipo") == tipo).then(pl.lit(tipo))
    df = (
        caixa.with_columns(grupo.otherwise(None).alias("grupo"))
        .filter(pl.col("grupo").is_not_null())
        .with_columns(
            pl.when(pl.col("grupo") == GRUPO_ENTRADAS).then(pl.col("amount_signed"))
            .otherwise(-pl.col("amount_signed")).alias("valor")
        )
        .group_by("anomes_key", "grupo", "categoria", maintain_order=True)
        .agg(pl.col("valor").sum())
    )
    return df.cast(_SCHEMA_CATEGORIA).select(list(_SCHEMA_CATEGORIA)).sort("anomes_key", "grupo", "categoria")
