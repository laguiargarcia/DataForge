"""conferencia_fatura.py — conferência do cartão pela fatura (df-acz4): o total de cada fatura fechada é o juiz
dos lançamentos do cartão.

O cartão não tem saldo por coleta como a conta BANK (conferencia_saldo.py); tem a fatura. Para cada fatura
fechada de curated.fact_card_statement (o total que o Pluggy devolve em /bills, sem os gêmeos reemitidos),
duas contas:

- lançamentos: total_fatura = saldo_anterior + soma dos lançamentos da fatura (o cc_bill_id deles), sem os
  pagamentos de fatura. saldo_anterior é o que ficou da fatura anterior sem pagar (total - pago dela, nunca
  negativo: um pagamento a mais aparece só na coluna do pagamento). diferenca_lancamentos = total_fatura -
  saldo_anterior - soma_lancamentos: negativa quando sobra um lançamento (a compra copiada da df-mk25),
  positiva quando falta um.
- pagamento: os pagamentos de fatura lançados no cartão contam para a fatura de vencimento mais perto da
  data deles, a até JANELA_PAGAMENTO_DIAS dias. O billId do pagamento não serve: até 03/2026 ele é o da
  fatura paga, depois o da fatura seguinte. diferenca_pagamento = total_fatura - pago: negativa quando o
  cartão tem pagamento a mais (a cópia de pagamento da df-75ol), positiva quando falta pagar.

Pagamento de fatura é um lançamento do cartão com amount < 0 e categoryId de CATEGORIAS_PAGAMENTO. O resto
(compras, encargos, estornos, créditos de acerto) é lançamento.

Estados (cada conta tem o seu):
- fecha: |diferenca| <= TOLERANCIA;
- historico_incompleto: não fecha, e o histórico de lançamentos do cartão começa depois do início do que a
  conta olha (o ciclo da fatura, DIAS_DO_CICLO antes do vencimento; ou a janela do pagamento);
- a_vencer (só pagamento): não fecha, e a janela do pagamento ainda não terminou no dia da rodada;
- sem_total: a fatura não tem total;
- nao_fecha: o resto.

conferencia_fatura só mede: não altera lançamento nenhum (df-z4fh). copias_pela_fatura usa a mesma conta para
decidir as cópias que a fatura prova (df-mk25): o Pluggy devolve um lançamento do cartão sob dois ids, a fatura
sobra exatamente o valor da cópia, e o raw2cleansed tira as que esta função aponta. Funções puras, sem I/O.
"""
import itertools
from datetime import date, timedelta

import polars as pl

# |diferenca| até aqui fecha. Medido no dado real (df-acz4): toda fatura que fecha fecha no centavo exato.
TOLERANCIA = 0.01

# Pagamento de fatura no cartão. Medido no dado real (df-acz4): 05100000 ('Credit card payment') até 06/2026;
# desde 07/2026 o débito automático chega como 05060000 ('Transfer - Internal'), que no cartão só pode ser
# pagamento.
CATEGORIAS_PAGAMENTO = ("05100000", "05060000")

# O pagamento cai até 2 dias depois do vencimento no dado real; 10 dias acha um pagamento adiantado sem
# alcançar o vencimento vizinho (cerca de 30 dias).
JANELA_PAGAMENTO_DIAS = 10

# Uma fatura cobre um ciclo de cerca de 30 dias que fecha uns 10 dias antes do vencimento.
DIAS_DO_CICLO = 40

FECHA, NAO_FECHA, HISTORICO_INCOMPLETO, A_VENCER, SEM_TOTAL = (
    "fecha", "nao_fecha", "historico_incompleto", "a_vencer", "sem_total")
ESTADOS_LANCAMENTOS = (FECHA, NAO_FECHA, HISTORICO_INCOMPLETO, SEM_TOTAL)
ESTADOS_PAGAMENTO = (FECHA, NAO_FECHA, HISTORICO_INCOMPLETO, A_VENCER, SEM_TOTAL)

COLUNAS = {
    "account_uuid": pl.String,
    "statement_id": pl.String,
    "due_date": pl.Date,
    "total_fatura": pl.Float64,
    "saldo_anterior": pl.Float64,
    "soma_lancamentos": pl.Float64,
    "n_lancamentos": pl.Int64,
    "diferenca_lancamentos": pl.Float64,
    "estado_lancamentos": pl.String,
    "pago": pl.Float64,
    "n_pagamentos": pl.Int64,
    "diferenca_pagamento": pl.Float64,
    "estado_pagamento": pl.String,
}


def eh_pagamento() -> pl.Expr:
    return (pl.col("amount") < 0) & pl.col("categoryId").is_in(CATEGORIAS_PAGAMENTO)


def _mes(col: str) -> pl.Expr:
    return (pl.col(col).dt.year() * 100 + pl.col(col).dt.month()).alias("mes")


def _fatura_de_cada_bill(faturas: pl.DataFrame, bills: pl.DataFrame) -> pl.DataFrame:
    # cc_bill_id -> statement_id. Uma fatura é ela mesma; um gêmeo reemitido que fact_card_statement deixou de
    # fora é a fatura da mesma conta e mês de vencimento (a que ficou).
    proprias = faturas.select(pl.col("statement_id").alias("cc_bill_id"), "statement_id")
    gemeos = (
        bills.select("id", "account_uuid", _mes("due_date"))
        .join(faturas.select("statement_id", "account_uuid", _mes("due_date")), on=["account_uuid", "mes"])
        .select(pl.col("id").alias("cc_bill_id"), "statement_id")
    )
    return pl.concat([proprias, gemeos]).unique("cc_bill_id", keep="first", maintain_order=True)


def _lancamentos_por_fatura(lancamentos: pl.DataFrame, mapa: pl.DataFrame) -> pl.DataFrame:
    return (
        lancamentos.filter(~eh_pagamento())
        .join(mapa, on="cc_bill_id")
        .group_by("statement_id")
        .agg(pl.col("amount").sum().alias("soma_lancamentos"), pl.len().cast(pl.Int64).alias("n_lancamentos"))
    )


def _pagamentos_por_fatura(lancamentos: pl.DataFrame, faturas: pl.DataFrame) -> pl.DataFrame:
    # Cada pagamento vai para a fatura da mesma conta com o vencimento mais perto da data dele, dentro da
    # janela; fora dela não conta para fatura nenhuma.
    vencimentos = faturas.select("account_uuid", "statement_id", "due_date").drop_nulls("due_date")
    return (
        lancamentos.filter(eh_pagamento()).drop_nulls("date").sort("date")
        .join_asof(vencimentos.sort("due_date"), left_on="date", right_on="due_date", by="account_uuid",
                   strategy="nearest", tolerance=timedelta(days=JANELA_PAGAMENTO_DIAS), check_sortedness=False)
        .drop_nulls("statement_id")
        .group_by("statement_id")
        .agg((-pl.col("amount")).sum().alias("pago"), pl.len().cast(pl.Int64).alias("n_pagamentos"))
    )


def _estado(diferenca: str, incompleto: pl.Expr, a_vencer: pl.Expr) -> pl.Expr:
    return (
        pl.when(pl.col("total_fatura").is_null()).then(pl.lit(SEM_TOTAL))
        .when(pl.col(diferenca).abs() <= TOLERANCIA).then(pl.lit(FECHA))
        .when(incompleto).then(pl.lit(HISTORICO_INCOMPLETO))
        .when(a_vencer).then(pl.lit(A_VENCER))
        .otherwise(pl.lit(NAO_FECHA))
    )


def conferencia_fatura(faturas: pl.DataFrame, bills: pl.DataFrame, lancamentos: pl.DataFrame,
                       hoje: date) -> pl.DataFrame:
    """Uma linha por fatura fechada com as diferenças dos lançamentos e do pagamento contra o total.

    faturas: curated.fact_card_statement (account_uuid, statement_id, due_date, statement_amount).
    bills: cleansed.bills com o account_uuid (id, account_uuid, due_date como Date), para os gêmeos.
    lancamentos: os lançamentos das contas de cartão de cleansed.transactions com o account_uuid
      (account_uuid, date, amount, categoryId, cc_bill_id).
    hoje: o dia da rodada (a_vencer).
    """
    mapa = _fatura_de_cada_bill(faturas, bills)
    inicio = lancamentos.group_by("account_uuid").agg(pl.col("date").min().alias("inicio"))
    sem_historico = pl.col("inicio").is_null()
    return (
        faturas.select("account_uuid", "statement_id", "due_date",
                       pl.col("statement_amount").alias("total_fatura"))
        .join(_lancamentos_por_fatura(lancamentos, mapa), on="statement_id", how="left")
        .join(_pagamentos_por_fatura(lancamentos, faturas), on="statement_id", how="left")
        .join(inicio, on="account_uuid", how="left")
        .with_columns(pl.col("soma_lancamentos", "pago").fill_null(0.0).round(2),
                      pl.col("n_lancamentos", "n_pagamentos").fill_null(0))
        .sort("account_uuid", "due_date", "statement_id")
        .with_columns(
            (pl.col("total_fatura") - pl.col("pago")).shift(1).over("account_uuid")
            .fill_null(0.0).clip(lower_bound=0.0).round(2).alias("saldo_anterior"))
        .with_columns(
            (pl.col("total_fatura") - pl.col("saldo_anterior") - pl.col("soma_lancamentos"))
            .round(2).alias("diferenca_lancamentos"),
            (pl.col("total_fatura") - pl.col("pago")).round(2).alias("diferenca_pagamento"))
        .with_columns(
            _estado("diferenca_lancamentos",
                    sem_historico | (pl.col("due_date") - timedelta(days=DIAS_DO_CICLO) < pl.col("inicio")),
                    pl.lit(False)).alias("estado_lancamentos"),
            _estado("diferenca_pagamento",
                    sem_historico
                    | (pl.col("due_date") - timedelta(days=JANELA_PAGAMENTO_DIAS) < pl.col("inicio")),
                    pl.col("due_date") + timedelta(days=JANELA_PAGAMENTO_DIAS) >= pl.lit(hoje))
            .alias("estado_pagamento"))
        .select(list(COLUNAS))
        .cast(COLUNAS)
    )


# Cópias que a fatura prova (df-mk25). Candidatos: lançamentos que não são pagamento, da mesma fatura (o
# statement, gêmeos reemitidos juntos), com mesmo valor e mesmo tipo; o dia e a descrição não entram (a cópia
# pode vir com outro texto ou outra data; uma compra repetida de verdade tem a fatura que fecha).
CHAVE_COPIA = ["account_uuid", "statement_id", "amount", "txn_type"]

# Acima disto de combinações a fatura não diz quais são as cópias: nada sai, e a fatura vira achado.
MAX_COMBINACOES = 4096

COPIAS_COLUNAS = {"id": pl.String, "mantido": pl.String, "account_uuid": pl.String, "statement_id": pl.String}
POR_FATURA_COLUNAS = {"statement_id": pl.String, "candidatos": pl.Int64, "removidos": pl.Int64,
                      "retida": pl.Boolean}


def _centavos(valor: float) -> int:
    return round(valor * 100)


def _combinacoes(grupos: list[pl.DataFrame], sobra: int) -> list[tuple[int, ...]] | None:
    # Quantas cópias tirar de cada grupo (0 até o tamanho dele menos 1: o primeiro sempre fica) para a fatura
    # fechar: a soma do que sai é a sobra, a até um centavo (TOLERANCIA). None quando são combinações demais.
    total = 1
    for g in grupos:
        total *= g.height
    if total > MAX_COMBINACOES:
        return None
    valores = [_centavos(g["amount"][0]) for g in grupos]
    return [n for n in itertools.product(*(range(g.height) for g in grupos))
            if any(n) and abs(sum(k * v for k, v in zip(n, valores, strict=True)) - sobra) <= _centavos(TOLERANCIA)]


def _ordem_clara(grupo: pl.DataFrame) -> bool:
    # Dá para dizer qual foi criado primeiro: todo createdAt existe e nenhum se repete.
    criado = grupo["criado"]
    return criado.null_count() == 0 and criado.n_unique() == grupo.height


def copias_pela_fatura(faturas: pl.DataFrame, bills: pl.DataFrame, lancamentos: pl.DataFrame,
                       hoje: date) -> tuple[pl.DataFrame, pl.DataFrame]:
    """As cópias que a fatura prova, e o que cada fatura com candidatos decidiu.

    faturas, bills e hoje: os de conferencia_fatura. lancamentos: os de conferencia_fatura mais id, txn_type
      e createdAt (texto ISO do Pluggy).
    Uma fatura com estado_lancamentos nao_fecha cujos lançamentos sobram perde cópias só quando uma única
    combinação de candidatos soma a sobra; de cada grupo saem os de createdAt mais recente. Mais de uma
    combinação, combinações demais ou createdAt ausente ou repetido: nada sai, e a fatura fica retida (um
    achado). Nenhuma combinação: a sobra não é de cópia, nada sai e não há achado (a conferência mostra a
    fatura). Fatura que fecha, sem total, com histórico incompleto ou ainda aberta (fora de faturas): nada sai.

    Devolve (copias, por_fatura): copias tem uma linha por id que sai (id, o id mantido do grupo, account_uuid,
    statement_id); por_fatura, uma por fatura que não fecha e tem candidatos (statement_id, candidatos,
    removidos, retida).
    """
    conferencia = conferencia_fatura(faturas, bills, lancamentos, hoje)
    candidatos = (
        lancamentos.filter(~eh_pagamento(), pl.col("amount").is_not_null())
        .join(_fatura_de_cada_bill(faturas, bills), on="cc_bill_id")
        .filter(pl.len().over(CHAVE_COPIA) > 1)
        .with_columns(pl.col("createdAt").str.to_datetime(strict=False, time_zone="UTC").alias("criado"))
    )
    copias, por_fatura = [], []
    nao_fecham = conferencia.filter(pl.col("estado_lancamentos") == NAO_FECHA)
    for statement_id, diferenca in nao_fecham.select("statement_id", "diferenca_lancamentos").iter_rows():
        grupos = [g.sort("criado", nulls_last=True) for _, g in
                  candidatos.filter(pl.col("statement_id") == statement_id)
                  .group_by(CHAVE_COPIA, maintain_order=True)]
        if not grupos:
            continue
        sols = _combinacoes(grupos, _centavos(-diferenca))
        unica = sols is not None and len(sols) == 1
        tirar = [(n, g) for n, g in zip(sols[0], grupos, strict=True) if n] if unica else []
        retida = sols is None or len(sols) > 1 or not all(_ordem_clara(g) for _, g in tirar)
        if not retida:
            for n, g in tirar:
                mantido = g["id"][0]
                copias += [(i, mantido, g["account_uuid"][0], statement_id) for i in g["id"].tail(n)]
        por_fatura.append((statement_id, sum(g.height for g in grupos),
                           0 if retida else sum(n for n, _ in tirar), retida))
    return (pl.DataFrame(copias, schema=COPIAS_COLUNAS, orient="row"),
            pl.DataFrame(por_fatura, schema=POR_FATURA_COLUNAS, orient="row"))
