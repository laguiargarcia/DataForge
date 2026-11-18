"""veredito_extrato.py — o extrato do banco dá veredito a cada lançamento suspeito (df-z4fh.3).

Com o extrato já lido (curated.extrato e curated.extrato_saldos, extrato.py), cada lançamento de
cleansed.transactions é casado com uma linha do extrato, e só então se lê o veredito de cada suspeito
(curated.lancamentos_suspeitos). Casar todos, e não só os suspeitos, impede que um suspeito tome o par de um
lançamento normal de mesmo valor.

O casamento (casar):
- mesma conta e mesmo valor, ao centavo (o sinal é o mesmo nos dois: saída é negativa);
- dia: o dia do lançamento no fuso do banco (FUSO) é o instante de raw (conferencia_saldo.instantes, em UTC)
  convertido; a diferença para o dia da linha é de no máximo TOLERANCIA_DIAS, para os dois lados. Um id sem
  hora em raw (instantes cai na meia-noite UTC do dia de cleansed) fica com o dia de cleansed, como um
  instante de raw de meia-noite UTC exata, que o dado real não tem (o fim do dia do Pluggy é 02:59 UTC);
- um para um, guloso e determinístico: os pares candidatos vão do de menor distância em dias para o de maior
  e, na mesma distância, pelo dia do lançamento, o POSTED antes do resto (o extrato lista o que foi lançado:
  um PENDING igual é a duplicata), o id, o dia da linha e a ordem da linha no dia; cada par
  entra quando nem o lançamento nem a linha já têm par. O par mais perto ganha: numa fila de valores iguais
  em dias seguidos (rendimentos de centavos) outro arranjo poderia casar um par a mais, puxando os outros
  para longe; no extrato real são 2 de 1129 (notas da df-z4fh.3).

O período que o extrato cobre é, por conta, a união do que cada arquivo cobre: do dia seguinte ao seu
primeiro saldo (o saldo anterior do período, fim de um dia que o arquivo não lista) ou do seu primeiro
lançamento, o que vier antes, até o seu último dia com lançamento ou saldo. Arquivos que se tocam ou se
sobrepõem viram um período só; um buraco entre arquivos não é coberto.

O veredito de um lançamento:
- NO_EXTRATO: tem par;
- FORA_DO_EXTRATO: não tem par, e todo dia em que o par poderia estar (o dia, mais e menos a tolerância) está
  num mesmo período coberto: o extrato o veria se ele existisse;
- SEM_EXTRATO: o resto (conta sem extrato, como o cartão, ou dia perto da borda ou fora do período).

Só lê e mede: nada aqui muda lançamento, fato ou número do relatório (df-z4fh). Funções puras, sem I/O.
"""
import polars as pl

from conferencia_saldo import instantes

# Dias de diferença aceitos entre o dia do Pluggy (no fuso do banco) e o do extrato. Medido no extrato real
# (notas da df-z4fh.3): uma compra no débito chega a ser lançada pelo banco 4 dias depois da data do Pluggy;
# com 3, três compras reais ficam sem par; com 5, o único par a mais é um rendimento de centavos.
TOLERANCIA_DIAS = 4
FUSO = "America/Sao_Paulo"

NO_EXTRATO, FORA_DO_EXTRATO, SEM_EXTRATO = "no_extrato", "fora_do_extrato", "sem_extrato_no_periodo"
VEREDITOS = (NO_EXTRATO, FORA_DO_EXTRATO, SEM_EXTRATO)

COLUNAS_CASAMENTO = {
    "id": pl.String, "account_id": pl.String, "dia_banco": pl.Date, "amount": pl.Float64,
    "extrato_dia": pl.Date, "extrato_ordem": pl.Int64, "veredito": pl.String,
}
COLUNAS_SEM_LANCAMENTO = {"arquivo": pl.String, "account_id": pl.String, "dia": pl.Date, "ordem": pl.Int64,
                          "descricao": pl.String, "valor": pl.Float64}
COLUNAS_ACERTO = {"motivo": pl.String, "n": pl.Int64, "n_no_extrato": pl.Int64, "n_fora_do_extrato": pl.Int64}

_UM_DIA = pl.duration(days=1)
_LINHA = ["account_id", "dia", "ordem"]


def _centavos(col: str) -> pl.Expr:
    return (pl.col(col) * 100).round().cast(pl.Int64).alias("centavos")


def _dias_do_banco(transactions: pl.DataFrame, raw_transactions: pl.DataFrame) -> pl.DataFrame:
    # id, account_id, dia_banco, amount, status de cada lançamento de cleansed.
    meia_noite = pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
    return (
        instantes(transactions, raw_transactions)
        .join(transactions.select("id", "date", "status"), on="id", how="left")
        .select("id", "account_id",
                pl.when(pl.col("instante") == meia_noite).then(pl.col("date"))
                .otherwise(pl.col("instante").dt.convert_time_zone(FUSO).dt.date()).alias("dia_banco"),
                "amount", "status")
    )


def _cobertura(extrato: pl.DataFrame, saldos: pl.DataFrame) -> pl.DataFrame:
    # (account_id, inicio, fim): os períodos cobertos, um por bloco de arquivos que se tocam.
    por_arquivo = (
        pl.concat([
            extrato.select("account_id", "arquivo", pl.col("dia").alias("inicio"), pl.col("dia").alias("fim")),
            saldos.group_by("account_id", "arquivo")
            .agg((pl.col("dia").min() + _UM_DIA).alias("inicio"), pl.col("dia").max().alias("fim")),
        ])
        .group_by("account_id", "arquivo")
        .agg(pl.col("inicio").min(), pl.col("fim").max())
        .filter(pl.col("inicio") <= pl.col("fim"))
        .sort("account_id", "inicio")
    )
    ate_aqui = pl.col("fim").cum_max().shift(1).over("account_id")
    novo_bloco = ate_aqui.is_null() | (pl.col("inicio") > ate_aqui + _UM_DIA)
    return (
        por_arquivo.with_columns(novo_bloco.cum_sum().over("account_id").alias("bloco"))
        .group_by("account_id", "bloco")
        .agg(pl.col("inicio").min(), pl.col("fim").max())
        .select("account_id", "inicio", "fim")
    )


def _pares(lancamentos: pl.DataFrame, extrato: pl.DataFrame, tolerancia: int) -> pl.DataFrame:
    # id -> extrato_dia, extrato_ordem: o casamento guloso descrito no docstring do módulo.
    candidatos = (
        lancamentos.select("id", "account_id", "dia_banco", _centavos("amount"),
                           (pl.col("status") != "POSTED").fill_null(True).alias("nao_lancado"))
        .join(extrato.select(*_LINHA, _centavos("valor")), on=["account_id", "centavos"])
        .with_columns((pl.col("dia") - pl.col("dia_banco")).dt.total_days().abs().alias("distancia"))
        .filter(pl.col("distancia") <= tolerancia)
        .sort("distancia", "dia_banco", "nao_lancado", "id", "dia", "ordem")
    )
    com_par, linhas_usadas, pares = set(), set(), []
    for id_, conta, dia, ordem in candidatos.select("id", *_LINHA).iter_rows():
        if id_ in com_par or (conta, dia, ordem) in linhas_usadas:
            continue
        com_par.add(id_)
        linhas_usadas.add((conta, dia, ordem))
        pares.append((id_, dia, ordem))
    return pl.DataFrame(pares, schema={"id": pl.String, "extrato_dia": pl.Date, "extrato_ordem": pl.Int64},
                        orient="row")


def casar(transactions: pl.DataFrame, raw_transactions: pl.DataFrame, extrato: pl.DataFrame,
          saldos: pl.DataFrame, tolerancia: int = TOLERANCIA_DIAS) -> pl.DataFrame:
    """Cada lançamento de cleansed com o seu par no extrato (ou sem) e o seu veredito (COLUNAS_CASAMENTO).

    transactions: cleansed.transactions (id, accountId, date, amount, status); `date` é o dia.
    raw_transactions: raw/transactions de todas as partições (id, date, ingested_at; texto), para a hora.
    extrato: curated.extrato com account_id (o id da conta no Pluggy), arquivo, dia, ordem, valor.
    saldos: curated.extrato_saldos com account_id, arquivo, dia.
    """
    extrato = extrato.cast({"account_id": pl.String, "arquivo": pl.String, "dia": pl.Date, "ordem": pl.Int64,
                            "valor": pl.Float64})
    saldos = saldos.cast({"account_id": pl.String, "arquivo": pl.String, "dia": pl.Date})
    lancamentos = _dias_do_banco(transactions, raw_transactions)
    cobertos = (
        lancamentos.join(_cobertura(extrato, saldos), on="account_id")
        .filter((pl.col("inicio") <= pl.col("dia_banco") - pl.duration(days=tolerancia))
                & (pl.col("dia_banco") + pl.duration(days=tolerancia) <= pl.col("fim")))
        .select("id").unique()
    )
    veredito = (pl.when(pl.col("extrato_dia").is_not_null()).then(pl.lit(NO_EXTRATO))
                .when(pl.col("id").is_in(cobertos["id"].implode())).then(pl.lit(FORA_DO_EXTRATO))
                .otherwise(pl.lit(SEM_EXTRATO)))
    return (
        lancamentos.join(_pares(lancamentos, extrato, tolerancia), on="id", how="left")
        .with_columns(veredito.alias("veredito"))
        .select(list(COLUNAS_CASAMENTO)).cast(COLUNAS_CASAMENTO)
        .sort("account_id", "dia_banco", "id")
    )


def vereditos(casamento: pl.DataFrame, suspeitos: pl.DataFrame) -> pl.DataFrame:
    """As linhas dos suspeitos (transaction_id, motivo e o que mais trouxerem), com veredito, dia_banco,
    extrato_dia e extrato_ordem. Um suspeito que não está no casamento fica SEM_EXTRATO."""
    return (
        suspeitos.join(casamento.select(pl.col("id").alias("transaction_id"), "veredito", "dia_banco",
                                        "extrato_dia", "extrato_ordem"), on="transaction_id", how="left",
                   maintain_order="left")
        .with_columns(pl.col("veredito").fill_null(SEM_EXTRATO))
    )


def sem_lancamento(extrato: pl.DataFrame, casamento: pl.DataFrame) -> pl.DataFrame:
    """As linhas do extrato (arquivo, account_id, dia, ordem, descricao, valor) que nenhum lançamento casou."""
    casadas = casamento.drop_nulls("extrato_dia").select(
        "account_id", pl.col("extrato_dia").alias("dia"), pl.col("extrato_ordem").alias("ordem"))
    return (
        extrato.select(list(COLUNAS_SEM_LANCAMENTO)).cast(COLUNAS_SEM_LANCAMENTO)
        .join(casadas, on=_LINHA, how="anti")
        .sort(_LINHA)
    )


def acerto_por_motivo(vereditos_suspeitos: pl.DataFrame, motivos: list[str]) -> pl.DataFrame:
    """Uma linha por motivo (os de `motivos`, na ordem, e qualquer outro que apareça): n suspeitos, quantos
    estão no extrato e quantos não estão. Os SEM_EXTRATO são n - n_no_extrato - n_fora_do_extrato."""
    contagem = vereditos_suspeitos.group_by("motivo").agg(
        pl.len().alias("n"),
        (pl.col("veredito") == NO_EXTRATO).sum().alias("n_no_extrato"),
        (pl.col("veredito") == FORA_DO_EXTRATO).sum().alias("n_fora_do_extrato"),
    )
    outros = sorted(set(contagem["motivo"].to_list()) - set(motivos))
    ordem = pl.DataFrame({"motivo": [*motivos, *outros]}, schema={"motivo": pl.String})
    return (
        ordem.join(contagem, on="motivo", how="left", maintain_order="left")
        .fill_null(0)
        .select(list(COLUNAS_ACERTO)).cast(COLUNAS_ACERTO)
    )
