"""conferencia_saldo.py — conferência pelo saldo (df-z4fh.1): o saldo da conta é o juiz dos lançamentos.

Em raw/accounts o balance de uma conta BANK é o total (conta + aplicação automática). Entre dois snapshots
seguidos (updatedAt distintos), a variação do saldo deve ser a soma dos lançamentos da conta com
de < instante <= ate. A diferença de um intervalo é variacao_saldo - soma_lancamentos: negativa quando falta
um lançamento de saída (ou sobra um de entrada), positiva no caso contrário.

Os lançamentos são os de cleansed.transactions (o conjunto que o relatório soma), menos as linhas de
aplicação/resgate automático (classificacao.eh_aplicacao_automatica, df-7twv.2): movimento interno. O instante de cada um vem
de raw/transactions (o `date` completo da coleta mais nova do id): cleansed guarda só o dia, e sem a hora
um lançamento do fim da tarde cai do lado errado do sync do mesmo dia. Um id sem hora em raw entra pelo
dia de cleansed, lido como a meia-noite UTC.

Cada intervalo tem um `estado`:
- fecha: |diferenca| <= TOLERANCIA;
- fecha_com_vizinho: não fecha sozinho, mas a soma da sua diferença com a de UM vizinho imediato (que
  também não fecha sozinho) fica dentro da TOLERANCIA. O banco às vezes move o saldo uma coleta antes da
  data do lançamento; cada intervalo entra em no máximo um par;
- em_aberto: o intervalo mais novo da conta, quando não fecha sozinho nem com o anterior: o vizinho
  seguinte ainda não existe;
- historico_incompleto: o intervalo mais velho da conta, quando não fecha sozinho nem com o seguinte: o
  vizinho anterior é de antes da primeira coleta de saldo (df-y26v; na conta principal, o seguro do cartão
  que o banco tirou do saldo antes da primeira coleta, a metade de um par fecha_com_vizinho). Numa conta com
  um intervalo só, ele é o mais novo e fica em_aberto;
- nao_fecha: o resto.
A `diferenca` crua fica sempre na linha, e `coletado_em` é o dia em que o snapshot `ate` chegou pela
primeira vez (a regra do aviso, em functions.py, olha se o snapshot mais novo da conta chegou na rodada).

Só mede: nada aqui altera lançamento nenhum (df-z4fh). Funções puras, sem I/O.
"""
import polars as pl

from classificacao import eh_aplicacao_automatica

# |diferenca| até aqui fecha. Medido no dado real (notas da df-z4fh.1): de junho a 08/09/2026 um intervalo
# que fecha erra no máximo 0,11 (rendimento da aplicação automática entre a data e o sync) e um par de
# horário soma no máximo 0,16; as quebras reais de setembro e outubro passam de 100. 1,00 deixa folga para
# o rendimento crescer com o saldo sem esconder uma quebra.
TOLERANCIA = 1.00

FECHA, FECHA_COM_VIZINHO, EM_ABERTO, HISTORICO_INCOMPLETO, NAO_FECHA = (
    "fecha", "fecha_com_vizinho", "em_aberto", "historico_incompleto", "nao_fecha")
ESTADOS = (FECHA, FECHA_COM_VIZINHO, EM_ABERTO, HISTORICO_INCOMPLETO, NAO_FECHA)

COLUNAS = {
    "account_id": pl.String,
    "de": pl.Datetime("us", "UTC"),
    "ate": pl.Datetime("us", "UTC"),
    "coletado_em": pl.Date,
    "saldo_de": pl.Float64,
    "saldo_ate": pl.Float64,
    "variacao_saldo": pl.Float64,
    "soma_lancamentos": pl.Float64,
    "n_lancamentos": pl.Int64,
    "diferenca": pl.Float64,
    "diferenca_acumulada": pl.Float64,
    "estado": pl.String,
}


def _instante(col: str) -> pl.Expr:
    return pl.col(col).str.to_datetime(time_unit="us", time_zone="UTC", strict=False)


def _snapshots(accounts_raw: pl.DataFrame) -> pl.DataFrame:
    # Um snapshot por (conta BANK, updatedAt): coletas que repetem o updatedAt são o mesmo snapshot. O saldo
    # é o da coleta mais nova; coletado_em é o dia da primeira coleta que o trouxe (uma coleta que só
    # repete o updatedAt não é snapshot novo). Um snapshot sem updatedAt legível ou sem saldo não tem como
    # entrar na conta.
    return (
        accounts_raw.filter(pl.col("type") == "BANK")
        .select(
            pl.col("id").alias("account_id"),
            _instante("updatedAt").alias("ate"),
            pl.col("balance").cast(pl.Float64, strict=False).alias("saldo_ate"),
            pl.col("ingested_at"),
            pl.col("ingested_at").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("coletado_em"),
        )
        .drop_nulls(["ate", "saldo_ate"])
        .sort("account_id", "ate", "ingested_at")
        .group_by("account_id", "ate", maintain_order=True)
        .agg(pl.col("saldo_ate").last(), pl.col("coletado_em").min())
    )


def instantes(transactions: pl.DataFrame, raw_transactions: pl.DataFrame) -> pl.DataFrame:
    # id, account_id, instante, amount de cada lançamento de cleansed, com a hora da coleta mais nova em raw.
    # Também diz em que intervalo cai cada suspeito (lancamentos_suspeitos.py).
    hora = (
        raw_transactions.sort("ingested_at")
        .unique("id", keep="last")
        .select("id", _instante("date").alias("instante_raw"))
    )
    return (
        transactions.join(hora, on="id", how="left")
        .select(
            pl.col("id"),
            pl.col("accountId").alias("account_id"),
            pl.coalesce("instante_raw",
                        pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone("UTC")).alias("instante"),
            pl.col("amount"),
        )
        .drop_nulls()
    )


def _estados(diferencas: list[float]) -> list[str]:
    # Os estados de uma conta, na ordem dos intervalos. Da esquerda para a direita, um intervalo que não
    # fecha sozinho forma par com o seguinte quando o seguinte também não fecha e a soma fica na tolerância.
    sozinho = [abs(d) <= TOLERANCIA for d in diferencas]
    estados = [FECHA if s else NAO_FECHA for s in sozinho]
    i = 0
    while i < len(diferencas) - 1:
        if not sozinho[i] and not sozinho[i + 1] and abs(diferencas[i] + diferencas[i + 1]) <= TOLERANCIA:
            estados[i] = estados[i + 1] = FECHA_COM_VIZINHO
            i += 2
        else:
            i += 1
    if estados and estados[0] == NAO_FECHA:
        estados[0] = HISTORICO_INCOMPLETO
    if estados and estados[-1] in (NAO_FECHA, HISTORICO_INCOMPLETO):
        estados[-1] = EM_ABERTO
    return estados


def conferencia_saldo(accounts_raw: pl.DataFrame, transactions: pl.DataFrame,
                      raw_transactions: pl.DataFrame) -> pl.DataFrame:
    """Uma linha por (conta BANK, par de snapshots seguidos) com a diferença entre saldo e lançamentos.

    accounts_raw: raw/accounts de todas as partições (id, type, balance, updatedAt, ingested_at; texto).
    transactions: cleansed.transactions (id, accountId, date, amount, description); `date` é o dia.
    raw_transactions: raw/transactions de todas as partições (id, date, ingested_at; texto), só para a hora.
    """
    snapshots = _snapshots(accounts_raw)
    intervalos = (
        snapshots
        .with_columns(
            pl.col("ate").shift(1).over("account_id").alias("de"),
            pl.col("saldo_ate").shift(1).over("account_id").alias("saldo_de"),
        )
        .drop_nulls("de")
    )
    if intervalos.is_empty():
        return pl.DataFrame(schema=COLUNAS)
    # Cada lançamento vai para o primeiro snapshot com ate >= instante: o anterior a ele tem de < instante.
    # O que cai no primeiro snapshot da conta (antes de toda conferência) ou depois do último fica de fora.
    # Os dois lados vão ordenados por instante (por conta, o join_asof não confere a ordem sozinho).
    # A linha de aplicação/resgate automático não entra: o saldo é o total (conta + aplicação), e ela só move
    # dinheiro entre os dois (df-7twv.2).
    somas = (
        instantes(transactions.filter(~eh_aplicacao_automatica()), raw_transactions)
        .sort("instante")
        .join_asof(snapshots.select("account_id", "ate").sort("ate"), left_on="instante", right_on="ate",
                   by="account_id", strategy="forward", check_sortedness=False)
        .group_by("account_id", "ate")
        .agg(pl.col("amount").sum().alias("soma_lancamentos"), pl.len().cast(pl.Int64).alias("n_lancamentos"))
    )
    out = (
        intervalos.join(somas, on=["account_id", "ate"], how="left")
        .with_columns(
            pl.col("soma_lancamentos").fill_null(0.0).round(2),
            pl.col("n_lancamentos").fill_null(0),
            (pl.col("saldo_ate") - pl.col("saldo_de")).round(2).alias("variacao_saldo"),
            (pl.col("saldo_ate") - pl.col("saldo_de") - pl.col("soma_lancamentos").fill_null(0.0))
            .round(2).alias("diferenca"),
        )
        .sort("account_id", "ate")
        .with_columns(pl.col("diferenca").cum_sum().over("account_id").round(2).alias("diferenca_acumulada"))
    )
    estados = [e for _, conta in out.group_by("account_id", maintain_order=True)
               for e in _estados(conta["diferenca"].to_list())]
    return out.with_columns(pl.Series("estado", estados, dtype=pl.String)).select(list(COLUNAS)).cast(COLUNAS)
