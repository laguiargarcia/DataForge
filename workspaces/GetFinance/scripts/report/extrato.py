"""extrato.py — o extrato do banco em PDF (df-z4fh.5): o texto das páginas vira lançamentos e saldos do dia.

O banco só exporta o extrato da conta corrente em PDF. O layout lido (o do PDF do ano exportado pelo app do
banco, lido à mão na df-7twv) é este, do mais novo para o mais velho:
- a primeira página tem um cabeçalho com "agência: <n> conta: <n>-<d>" e "Emitido em: dd/mm/aaaa hh:mm:ss";
- depois vem "Lançamentos futuros" (agendados, ainda não aconteceram: ficam de fora) e a linha "Lançamentos",
  que abre a lista que vale; "Posição consolidada" a fecha (sem ela o documento está cortado: erro);
- cada página repete a linha de títulos das colunas (CABECALHO_COLUNAS);
- um lançamento começa com a data e a descrição; o valor, com sinal (saída negativa), fecha a linha ou vem
  numa linha seguinte, quando a descrição quebra em mais de uma linha (até numa página seguinte);
- uma linha só com data e valor é o saldo do fim daquele dia. Como a lista vem do mais novo para o mais
  velho, a última é o saldo do dia anterior ao primeiro lançamento (o saldo anterior do período).
Uma linha fora de lançamento que não é nada disso (um título) é ignorada e contada.

A conta do extrato é casada em tempo de execução com cleansed.accounts pela agência e pelo número do
cabeçalho, para que nenhum número de conta precise morar no código.

Vários arquivos podem cobrir o mesmo dia. Para cada (conta, dia), os lançamentos vêm de um arquivo só: o que
traz mais lançamentos daquele dia, depois o emitido mais tarde, depois o nome. Assim um dia não aparece duas
vezes (nem perde um de dois lançamentos iguais), e um arquivo que só traz o saldo do dia (o saldo anterior
do seu período) não apaga os lançamentos que outro traz. O saldo do dia vem de qualquer arquivo que o traga;
dois arquivos com saldos diferentes para o mesmo dia são um erro (SaldosDivergentes) que nomeia os dois.

A conferência por dia: o saldo do dia anterior que tem saldo, mais os lançamentos depois dele até o dia,
menos o saldo do dia. Só mede: nada aqui mexe em lançamento do Pluggy (df-z4fh). Funções puras, sem I/O.
"""
import re
from datetime import datetime
from typing import NamedTuple

import polars as pl

# Meio centavo: os valores do extrato vêm em centavos, então um dia que fecha erra zero.
TOLERANCIA = 0.005

INICIO, FIM = "Lançamentos", "Posição consolidada"
CABECALHO_COLUNAS = "Data Lançamentos Descrição Valor (R$) Saldo (R$)"

_DATA = r"\d{2}/\d{2}/\d{4}"
_VALOR = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
_SALDO = re.compile(rf"({_DATA}) ({_VALOR})")
_LANCAMENTO = re.compile(rf"({_DATA}) (.+)")
_FECHA_COM_VALOR = re.compile(rf"(?:(.*?)\s+)?({_VALOR})")
_CONTA = re.compile(r"agência:\s*(\d+)\s+conta:\s*([\d.-]+)")
_EMITIDO = re.compile(rf"Emitido em:\s*({_DATA} \d{{2}}:\d{{2}}:\d{{2}})")

LANCAMENTOS = {"dia": pl.Date, "ordem": pl.Int64, "descricao": pl.String, "valor": pl.Float64}
SALDOS = {"dia": pl.Date, "saldo": pl.Float64}
_ORIGEM = {"arquivo": pl.String, "emitido_em": pl.Datetime("us"), "account_id": pl.String}

COLUNAS = {"arquivo": pl.String, "account_id": pl.String, "dia": pl.Date, "ordem": pl.Int64,
           "descricao": pl.String, "valor": pl.Float64, "saldo_do_dia": pl.Float64}
COLUNAS_SALDOS = {"account_id": pl.String, "dia": pl.Date, "arquivo": pl.String, "saldo_do_dia": pl.Float64,
                  "saldo_anterior": pl.Float64, "soma_lancamentos": pl.Float64, "n_lancamentos": pl.Int64,
                  "diferenca": pl.Float64, "fecha": pl.Boolean}


class LayoutDesconhecido(ValueError):
    """O texto não tem o layout que este módulo lê: melhor parar do que gravar um extrato errado."""


class SaldosDivergentes(ValueError):
    """Dois arquivos trazem saldos diferentes para o mesmo dia da mesma conta: não há como escolher um."""


class Extrato(NamedTuple):
    agencia: str
    conta: str
    emitido_em: datetime
    lancamentos: pl.DataFrame   # LANCAMENTOS, na ordem do arquivo
    saldos: pl.DataFrame        # SALDOS
    ignoradas: int              # linhas da lista que não são lançamento nem saldo


def _valor(texto: str) -> float:
    return float(texto.replace(".", "").replace(",", "."))


def _dia(texto: str):
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date()   # noqa: DTZ007 -- a calendar day with no time
    except ValueError as erro:
        raise LayoutDesconhecido(f"data impossível {texto}") from erro


def _cabecalho(texto: str) -> tuple[str, str, datetime]:
    conta, emitido = _CONTA.search(texto), _EMITIDO.search(texto)
    if conta is None or emitido is None:
        raise LayoutDesconhecido("cabeçalho sem agência e conta ou sem a data de emissão")
    return (conta.group(1), re.sub(r"\D", "", conta.group(2)),
            datetime.strptime(emitido.group(1), "%d/%m/%Y %H:%M:%S"))   # noqa: DTZ007 -- the PDF prints local time with no zone


def _recorte(paginas: list[str]) -> tuple[list[str] | None, bool]:
    # As linhas da lista, entre INICIO e FIM, sem as vazias e sem os títulos das colunas que cada página repete;
    # None quando não há INICIO. O segundo valor diz se a lista chegou ao FIM.
    linhas = (linha.strip() for pagina in paginas for linha in pagina.splitlines())
    if INICIO not in linhas:   # consome até INICIO, inclusive
        return None, False
    lista = []
    for linha in linhas:
        if linha == FIM:
            return lista, True
        if linha and linha != CABECALHO_COLUNAS:
            lista.append(linha)
    return lista, False


class _Leitura:
    """Percorre as linhas da lista: lançamentos, saldos do dia e linhas ignoradas."""

    def __init__(self):
        self.lancamentos, self.saldos, self.ignoradas = [], [], 0
        self.aberto = None   # [dia, partes da descrição] do lançamento que ainda espera o valor

    def sem_valor(self) -> LayoutDesconhecido:
        return LayoutDesconhecido(f"lançamento de {self.aberto[0]} sem valor")

    def linha(self, linha: str) -> None:
        saldo, inicio = _SALDO.fullmatch(linha), _LANCAMENTO.fullmatch(linha)
        if (saldo or inicio) and self.aberto is not None:
            raise self.sem_valor()
        if saldo:
            self.saldos.append((_dia(saldo.group(1)), _valor(saldo.group(2))))
        elif inicio:
            self.aberto = [_dia(inicio.group(1)), []]
            self._descricao(inicio.group(2))
        elif self.aberto is None:
            self.ignoradas += 1
        else:
            self._descricao(linha)

    def _descricao(self, texto: str) -> None:
        # Mais um pedaço da descrição do lançamento aberto; o valor no fim do pedaço fecha o lançamento.
        fim = _FECHA_COM_VALOR.fullmatch(texto)
        dia, partes = self.aberto
        if fim is None:
            partes.append(texto)
            return
        partes.append(fim.group(1) or "")
        self.lancamentos.append((dia, " ".join(p for p in partes if p), _valor(fim.group(2))))
        self.aberto = None


def ler(paginas: list[str]) -> Extrato:
    """O texto de cada página (na ordem do PDF) -> o extrato: cabeçalho, lançamentos e saldos do dia."""
    if not paginas:
        raise LayoutDesconhecido("PDF sem páginas")
    agencia, conta, emitido_em = _cabecalho(paginas[0])
    lista, fechou = _recorte(paginas)
    if lista is None:
        raise LayoutDesconhecido(f"sem a linha {INICIO!r} que abre a lista")
    leitura = _Leitura()
    for linha in lista:
        leitura.linha(linha)
    if leitura.aberto is not None:
        raise leitura.sem_valor()
    if not fechou:
        raise LayoutDesconhecido(f"sem a linha {FIM!r} que fecha a lista (documento cortado?)")
    frame = pl.DataFrame(leitura.lancamentos, schema={k: LANCAMENTOS[k] for k in ("dia", "descricao", "valor")},
                         orient="row")
    frame = frame.with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).over("dia").alias("ordem"))
    return Extrato(agencia, conta, emitido_em, frame.select(list(LANCAMENTOS)),
                   pl.DataFrame(leitura.saldos, schema=SALDOS, orient="row"), leitura.ignoradas)


def _digitos(col: pl.Expr) -> pl.Expr:
    return col.str.replace_all(r"\D", "").str.strip_chars_start("0")


def contas_do_extrato(extrato: Extrato, contas: pl.DataFrame) -> list[str]:
    """Os ids das contas BANK de cleansed.accounts (id, type, number, transfer_number) com a agência e a conta
    do cabeçalho. O transfer_number é banco/agência/conta (ou agência/conta); a conta também casa com
    `number`. Zeros à esquerda e separadores não contam."""
    partes = pl.col("transfer_number").str.split("/")
    agencia = extrato.agencia.lstrip("0")
    conta = extrato.conta.lstrip("0")
    return (
        contas.filter(pl.col("type") == "BANK")
        .with_columns(_digitos(partes.list.get(-2, null_on_oob=True)).alias("_agencia"),
                      _digitos(partes.list.get(-1, null_on_oob=True)).alias("_conta"),
                      _digitos(pl.col("number")).alias("_numero"))
        .filter((pl.col("_agencia") == agencia) & ((pl.col("_conta") == conta) | (pl.col("_numero") == conta)))
        ["id"].unique().sort().to_list()
    )


def _arquivo_dos_lancamentos(lancamentos: pl.DataFrame) -> pl.DataFrame:
    # (account_id, dia) -> o arquivo de onde vêm os lançamentos do dia: o que traz mais, depois o emitido mais
    # tarde, depois o nome (para o empate dar sempre o mesmo).
    return (
        lancamentos.group_by("account_id", "dia", "arquivo", "emitido_em").agg(pl.len().alias("n"))
        .sort(["n", "emitido_em", "arquivo"], descending=True)
        .unique(["account_id", "dia"], keep="first")
        .select("account_id", "dia", "arquivo")
    )


def _saldo_do_dia(saldos: pl.DataFrame) -> pl.DataFrame:
    # (account_id, dia) -> o saldo do dia e o arquivo (o emitido mais tarde) de onde vem. Saldos diferentes do
    # mesmo dia em arquivos diferentes param tudo, nomeando o dia e os arquivos (nunca o valor).
    divergentes = (
        saldos.group_by("account_id", "dia")
        .agg(pl.col("saldo").round(2).n_unique().alias("n"), pl.col("arquivo").unique().sort().alias("arquivos"))
        .filter(pl.col("n") > 1).sort("dia")
    )
    if divergentes.height:
        raise SaldosDivergentes("; ".join(f"{dia}: {', '.join(arquivos)}"
                                          for dia, arquivos in divergentes.select("dia", "arquivos").rows()))
    return (
        saldos.sort(["emitido_em", "arquivo"], descending=True)
        .unique(["account_id", "dia"], keep="first")
        .select("account_id", "dia", "arquivo", pl.col("saldo").alias("saldo_do_dia"))
    )


def juntar(lancamentos: pl.DataFrame, saldos: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Os extratos lidos -> (extrato, saldos do dia com a conferência).

    lancamentos: LANCAMENTOS de todos os arquivos, com arquivo, emitido_em e account_id de cada um.
    saldos: SALDOS de todos os arquivos, com as mesmas três colunas.
    extrato (COLUNAS): um lançamento por linha, os de cada (conta, dia) de um arquivo só, com o saldo do dia
    quando algum arquivo traz.
    saldos do dia (COLUNAS_SALDOS): um por (conta, dia com saldo); saldo_anterior é o do dia com saldo
    anterior, soma_lancamentos e n_lancamentos são os do extrato depois daquele dia até este, diferenca é
    saldo_anterior + soma_lancamentos - saldo_do_dia, e fecha diz se |diferenca| <= TOLERANCIA. O primeiro
    saldo de cada conta não tem anterior: fecha fica nulo. Levanta SaldosDivergentes quando dois arquivos
    discordam do saldo de um dia.
    """
    lancamentos = lancamentos.cast({**_ORIGEM, **LANCAMENTOS})
    saldos = saldos.cast({**_ORIGEM, **SALDOS})
    saldos = _saldo_do_dia(saldos)
    extrato = (
        lancamentos.join(_arquivo_dos_lancamentos(lancamentos), on=["account_id", "dia", "arquivo"], how="semi")
        .join(saldos.drop("arquivo"), on=["account_id", "dia"], how="left")
        .select(list(COLUNAS)).cast(COLUNAS)
        .sort("account_id", "dia", "ordem")
    )
    # Cada lançamento conta no primeiro dia com saldo a partir do seu (o join_asof para a frente).
    por_saldo = (
        extrato.select("account_id", "dia", "valor").sort("dia")
        .join_asof(saldos.select("account_id", pl.col("dia").alias("dia_saldo")).sort("dia_saldo"),
                   left_on="dia", right_on="dia_saldo", by="account_id", strategy="forward",
                   check_sortedness=False)
        .group_by("account_id", "dia_saldo")
        .agg(pl.col("valor").sum().alias("soma_lancamentos"), pl.len().cast(pl.Int64).alias("n_lancamentos"))
    )
    dias = (
        saldos.sort("account_id", "dia")
        .with_columns(pl.col("saldo_do_dia").shift(1).over("account_id").alias("saldo_anterior"))
        .join(por_saldo, left_on=["account_id", "dia"], right_on=["account_id", "dia_saldo"], how="left")
        .with_columns(pl.col("soma_lancamentos").fill_null(0.0), pl.col("n_lancamentos").fill_null(0))
        .with_columns((pl.col("saldo_anterior") + pl.col("soma_lancamentos") - pl.col("saldo_do_dia"))
                      .round(2).alias("diferenca"))
        .with_columns((pl.col("diferenca").abs() <= TOLERANCIA).alias("fecha"))
        .select(list(COLUNAS_SALDOS)).cast(COLUNAS_SALDOS)
    )
    return extrato, dias
