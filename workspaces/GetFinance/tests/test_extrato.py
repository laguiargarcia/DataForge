"""Extrato do banco em PDF (df-z4fh.5): o texto das páginas vira lançamentos e saldos do dia, e a task grava
curated.extrato, curated.extrato_saldos e curated.extrato_arquivos a partir da pasta data/extratos/.

A leitura pura (scripts/report/extrato.py) sobre texto no layout do PDF do banco, e a task rodada como
processo num workspace de mentira, com PDFs feitos aqui. Dados falsos: nenhum nome, conta, descrição ou
valor vem do extrato real.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import types
import zlib
from datetime import date, datetime
from typing import NamedTuple

import polars as pl
import pypdf
import pytest

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from extrato import (CABECALHO_COLUNAS, COLUNAS, COLUNAS_SALDOS, LANCAMENTOS, SALDOS, LayoutDesconhecido,
                     contas_do_extrato, dias_divergentes, guardados, juntar, ler, nome_guardado, periodo,
                     periodo_do_nome)

BANK = "p-bank-1"


def _cabecalho(emitido="05/06/2026 10:00:00", agencia="0001", conta="1234567-8"):
    # A primeira página até a linha que abre a lista, com um lançamento futuro que não conta.
    return "\n".join([
        "FULANO DE TAL",
        f"CPF: 000.000.000-00 agência: {agencia} conta: {conta}",
        "Saldo em conta", "R$ 100,00",
        "Extrato conta corrente",
        f"Período de visualização: 01/06/2026 até 05/06/2026 Emitido em: {emitido}",
        "Lançamentos futuros", "Saídas futuras", CABECALHO_COLUNAS,
        "30/06/2026 SAÍDA SEGURO FICTICIO -8,70",
        "Lançamentos", CABECALHO_COLUNAS,
    ])


RODAPE = "\n".join(["Posição consolidada", "Descrição Valor", "Saldo R$ 999,99",
                    "01/07/2026 NADA DEPOIS DAQUI CONTA -1,00"])


def _lista(*linhas, emitido="05/06/2026 10:00:00"):
    # Um arquivo de uma página: cabeçalho, as linhas da lista (do mais novo para o mais velho) e o fechamento.
    return [_cabecalho(emitido) + "\n" + "\n".join(linhas) + "\n" + RODAPE]


# Arquivo A: 31/05 (saldo anterior) a 05/06, do mais novo para o mais velho; 05/06 ainda sem saldo.
A = [_cabecalho() + "\n" + "\n".join([
    "05/06/2026 COMPRA DÉBITO PADARIA FICTICIA -10,00",
    "04/06/2026 1.240,00",
    "04/06/2026 PIX RECEBIDO CICLANO FICTICIO 250,00",
    "04/06/2026 COMPRA DÉBITO MERCADO FICTICIO -10,00",
    "02/06/2026 1.000,00",
    "02/06/2026 PAGAMENTO DE PIX QR",
    "CODE",
    "LOJA FICTICIA -50,00",
    "31/05/2026 1.050,00",
]) + "\n" + RODAPE]

# Arquivo B, emitido depois: 04/06 (saldo anterior, sem lançamentos) a 08/06; o sábado 06/06 não tem saldo.
B = [_cabecalho(emitido="08/06/2026 09:00:00") + "\n" + "\n".join([
    "08/06/2026 1.195,00",
    "08/06/2026 COMPRA DÉBITO FARMACIA FICTICIA -30,00",
    "06/06/2026 COMPRA DÉBITO BAR FICTICIO -5,00",
    "05/06/2026 1.230,00",
    "05/06/2026 COMPRA DÉBITO PADARIA FICTICIA -10,00",
    "04/06/2026 1.240,00",
]) + "\n" + RODAPE]


def _d(texto):
    return date.fromisoformat(texto)


def _lancamentos(lido):
    return lido.lancamentos.select("dia", "descricao", "valor").rows()


def _origem(lido, arquivo, account=BANK):
    return {"arquivo": pl.lit(arquivo), "emitido_em": pl.lit(lido.emitido_em), "account_id": pl.lit(account)}


def _quadros(*arquivos):
    # (lançamentos, saldos) de todos os arquivos, como a task monta para juntar().
    lidos = [(nome, ler(paginas)) for nome, paginas in arquivos]
    return (pl.concat([lido.lancamentos.with_columns(**_origem(lido, nome)) for nome, lido in lidos]),
            pl.concat([lido.saldos.with_columns(**_origem(lido, nome)) for nome, lido in lidos]))


def _juntar(*arquivos):
    return juntar(*_quadros(*arquivos))


# --- a leitura ------------------------------------------------------------------------------------

def test_cabecalho_da_agencia_conta_e_emissao():
    lido = ler(A)
    assert (lido.agencia, lido.conta, lido.emitido_em) == ("0001", "12345678", datetime(2026, 6, 5, 10, 0, 0))


def test_lancamento_de_debito_e_de_credito():
    lido = ler(A)
    assert (_d("2026-06-04"), "PIX RECEBIDO CICLANO FICTICIO", 250.0) in _lancamentos(lido)
    assert (_d("2026-06-04"), "COMPRA DÉBITO MERCADO FICTICIO", -10.0) in _lancamentos(lido)


def test_valor_com_milhar_e_saldo_negativo():
    lido = ler(_lista("03/06/2026 -1.234,56", "03/06/2026 TED RECEBIDA FICTICIA 12.345,67"))
    assert _lancamentos(lido) == [(_d("2026-06-03"), "TED RECEBIDA FICTICIA", 12345.67)]
    assert lido.saldos.rows() == [(_d("2026-06-03"), -1234.56)]


def test_linha_de_saldo_do_dia():
    assert ler(A).saldos.rows() == [(_d("2026-06-04"), 1240.0), (_d("2026-06-02"), 1000.0),
                                    (_d("2026-05-31"), 1050.0)]


def test_descricao_quebrada_em_linhas_e_valor_sozinho_na_linha():
    lido = ler(_lista("03/06/2026 COMPRA DÉBITO LOJA *EXEMPLO", "AJUDA.EXEMPLO.COM", "-12,30",
                      "02/06/2026 PAGAMENTO DE PIX QR", "CODE", "LOJA FICTICIA -50,00"))
    assert _lancamentos(lido) == [(_d("2026-06-03"), "COMPRA DÉBITO LOJA *EXEMPLO AJUDA.EXEMPLO.COM", -12.3),
                                  (_d("2026-06-02"), "PAGAMENTO DE PIX QR CODE LOJA FICTICIA", -50.0)]


def test_quebra_de_pagina_no_meio_de_um_lancamento():
    # A descrição começa no fim de uma página; a página seguinte repete os títulos e traz o resto e o valor.
    paginas = [_cabecalho() + "\n03/06/2026 COMPRA DÉBITO LOJA\n02/06/2026 1.000,00\n02/06/2026 PAGAMENTO DE PIX QR",
               CABECALHO_COLUNAS + "\nCODE\nLOJA FICTICIA -50,00\n01/06/2026 1.050,00\n" + RODAPE]
    with pytest.raises(LayoutDesconhecido):
        ler(paginas)   # 03/06 ficou sem valor antes do saldo de 02/06
    paginas[0] = paginas[0].replace("03/06/2026 COMPRA DÉBITO LOJA\n", "03/06/2026 COMPRA DÉBITO LOJA -1,00\n")
    lido = ler(paginas)
    assert _lancamentos(lido)[1] == (_d("2026-06-02"), "PAGAMENTO DE PIX QR CODE LOJA FICTICIA", -50.0)
    assert lido.saldos.height == 2
    assert lido.ignoradas == 0


def test_linha_que_nao_e_lancamento_e_ignorada_e_contada():
    lido = ler(_lista("UM TITULO NO MEIO", "03/06/2026 COMPRA DÉBITO LOJA -1,00", "", "OUTRO TITULO"))
    assert lido.ignoradas == 2
    assert _lancamentos(lido) == [(_d("2026-06-03"), "COMPRA DÉBITO LOJA", -1.0)]


def test_lancamentos_futuros_e_o_que_vem_depois_da_posicao_consolidada_ficam_de_fora():
    lido = ler(A)
    descricoes = lido.lancamentos["descricao"].to_list()
    assert not any("SEGURO" in d or "NADA DEPOIS" in d for d in descricoes)
    assert lido.lancamentos.height == 4


def test_ordem_dentro_do_dia():
    lido = ler(A)
    assert lido.lancamentos.filter(pl.col("dia") == _d("2026-06-04"))["ordem"].to_list() == [1, 2]
    assert lido.lancamentos.schema == pl.Schema(LANCAMENTOS)
    assert lido.saldos.schema == pl.Schema(SALDOS)


@pytest.mark.parametrize("paginas", [
    [],
    ["FULANO DE TAL\nExtrato conta corrente\nLançamentos\n03/06/2026 COMPRA -1,00"],   # sem agência e conta
    [_cabecalho().replace("\nLançamentos\n", "\nOutra Coisa\n")],                       # sem a linha da lista
    [_cabecalho() + "\n03/06/2026 COMPRA DÉBITO SEM VALOR"],                             # termina sem valor
    [_cabecalho() + "\n03/06/2026 COMPRA DÉBITO SEM VALOR\n03/06/2026 OUTRA COMPRA -1,00"],
    [_cabecalho() + "\n03/06/2026 COMPRA DÉBITO 12345,67"],                              # valor fora do formato
    [_cabecalho() + "\n03/06/2026 COMPRA DÉBITO LOJA -1,00"],                            # cortado: sem o fechamento
], ids=["sem-paginas", "sem-cabecalho", "sem-lista", "fim-sem-valor", "outro-lancamento-sem-valor",
        "valor-sem-milhar", "sem-fechamento"])
def test_layout_desconhecido_para_em_vez_de_gravar_errado(paginas):
    with pytest.raises(LayoutDesconhecido):
        ler(paginas)


def test_data_impossivel_para_e_diz_qual():
    paginas = _lista("31/02/2026 COMPRA DÉBITO LOJA -1,00")
    with pytest.raises(LayoutDesconhecido, match="data impossível 31/02/2026"):
        ler(paginas)


# --- a conta --------------------------------------------------------------------------------------

def _contas(*rows):
    return pl.DataFrame(list(rows), schema={"id": pl.String, "type": pl.String, "number": pl.String,
                                            "transfer_number": pl.String}, orient="row")


def test_conta_casa_pela_agencia_e_conta_sem_zeros_nem_separadores():
    contas = _contas((BANK, "BANK", "0001234567-8", "901/0001/0001234567-8"),
                     ("p-bank-2", "BANK", "00099999-9", "901/0001/00099999-9"),
                     ("p-card-1", "CREDIT", "0001234567-8", "901/0001/0001234567-8"))
    assert contas_do_extrato(ler(A), contas) == [BANK]


def test_conta_casa_pelo_number_e_por_agencia_conta_sem_banco():
    assert contas_do_extrato(ler(A), _contas((BANK, "BANK", "1234567-8", "0001/9999999-0"))) == [BANK]
    assert contas_do_extrato(ler(A), _contas((BANK, "BANK", None, "0001/1234567-8"))) == [BANK]


def test_conta_nao_casa_com_outra_agencia_ou_sem_transfer_number():
    assert contas_do_extrato(ler(A), _contas((BANK, "BANK", "1234567-8", "901/0002/1234567-8"))) == []
    assert contas_do_extrato(ler(A), _contas((BANK, "BANK", "1234567-8", None))) == []


# --- juntar e conferir ----------------------------------------------------------------------------

def test_um_arquivo_todos_os_dias_fecham_e_o_primeiro_nao_tem_anterior():
    extrato, dias = _juntar(("a.pdf", A))
    assert extrato.schema == pl.Schema(COLUNAS)
    assert dias.schema == pl.Schema(COLUNAS_SALDOS)
    assert dias.select("dia", "saldo_anterior", "soma_lancamentos", "n_lancamentos", "diferenca", "fecha").rows() == [
        (_d("2026-05-31"), None, 0.0, 0, None, None),
        (_d("2026-06-02"), 1050.0, -50.0, 1, 0.0, True),
        (_d("2026-06-04"), 1000.0, 240.0, 2, 0.0, True),
    ]
    # O dia sem saldo (05/06, ainda aberto) fica no extrato com saldo_do_dia nulo.
    assert extrato.filter(pl.col("dia") == _d("2026-06-05"))["saldo_do_dia"].to_list() == [None]
    assert extrato.filter(pl.col("dia") == _d("2026-06-04"))["saldo_do_dia"].to_list() == [1240.0, 1240.0]


def test_dia_cujo_saldo_nao_fecha_e_relatado():
    errado = [A[0].replace("04/06/2026 1.240,00", "04/06/2026 1.234,00")]
    _, dias = _juntar(("a.pdf", errado))
    linha = dias.filter(pl.col("dia") == _d("2026-06-04")).row(0, named=True)
    assert (linha["diferenca"], linha["fecha"]) == (6.0, False)
    assert dias.filter(~pl.col("fecha")).height == 1   # o nulo do primeiro saldo não conta


def test_lancamento_de_fim_de_semana_conta_no_proximo_dia_com_saldo():
    _, dias = _juntar(("b.pdf", B))
    linha = dias.filter(pl.col("dia") == _d("2026-06-08")).row(0, named=True)
    assert (linha["n_lancamentos"], linha["soma_lancamentos"], linha["fecha"]) == (2, -35.0, True)


def test_dois_arquivos_que_se_sobrepoem_nao_duplicam():
    extrato, dias = _juntar(("a.pdf", A), ("b.pdf", B))
    # 04/06 vem de A (B só traz o saldo dele, como saldo anterior); 05/06 (um lançamento em cada) vem de B, o
    # emitido mais tarde. O saldo de 04/06, igual nos dois, vem de A, o arquivo dos lançamentos do dia.
    assert extrato.select("dia", "arquivo").rows() == [
        (_d("2026-06-02"), "a.pdf"), (_d("2026-06-04"), "a.pdf"), (_d("2026-06-04"), "a.pdf"),
        (_d("2026-06-05"), "b.pdf"), (_d("2026-06-06"), "b.pdf"), (_d("2026-06-08"), "b.pdf")]
    assert dias.select("dia", "arquivo").rows() == [
        (_d("2026-05-31"), "a.pdf"), (_d("2026-06-02"), "a.pdf"), (_d("2026-06-04"), "a.pdf"),
        (_d("2026-06-05"), "b.pdf"), (_d("2026-06-08"), "b.pdf")]
    assert dias["fecha"].to_list() == [None, True, True, True, True]
    assert not dias["divergente"].any()


def test_saldo_de_abertura_de_um_arquivo_nao_apaga_os_lancamentos_do_outro():
    # O caso do revisor: A termina num sábado com dois lançamentos sem saldo; B abre com o saldo desse sábado
    # (o saldo anterior do seu período), sem lançamentos dele. Os lançamentos vêm de A, o saldo de B.
    a = _lista("06/06/2026 COMPRA DÉBITO BAR FICTICIO -5,00", "06/06/2026 COMPRA DÉBITO BANCA FICTICIA -15,00",
               "05/06/2026 1.000,00", emitido="06/06/2026 23:00:00")
    b = _lista("08/06/2026 970,00", "08/06/2026 COMPRA DÉBITO FARMACIA FICTICIA -10,00", "06/06/2026 980,00",
               emitido="09/06/2026 09:00:00")
    extrato, dias = _juntar(("a.pdf", a), ("b.pdf", b))
    sabado = extrato.filter(pl.col("dia") == _d("2026-06-06"))
    assert sabado.select("arquivo", "valor", "saldo_do_dia").rows() == [("a.pdf", -5.0, 980.0), ("a.pdf", -15.0, 980.0)]
    assert dias.select("dia", "arquivo", "n_lancamentos", "fecha").rows() == [
        (_d("2026-06-05"), "a.pdf", 0, None), (_d("2026-06-06"), "b.pdf", 2, True),
        (_d("2026-06-08"), "b.pdf", 1, True)]


def test_dois_lancamentos_iguais_no_mesmo_dia_ficam_dois_na_sobreposicao():
    linhas = ("03/06/2026 980,00", "03/06/2026 COMPRA DÉBITO CAFE FICTICIO -10,00",
              "03/06/2026 COMPRA DÉBITO CAFE FICTICIO -10,00", "02/06/2026 1.000,00")
    extrato, dias = _juntar(("a.pdf", _lista(*linhas)), ("b.pdf", _lista(*linhas, emitido="07/06/2026 10:00:00")))
    assert extrato.select("dia", "ordem", "valor", "arquivo").rows() == [
        (_d("2026-06-03"), 1, -10.0, "b.pdf"), (_d("2026-06-03"), 2, -10.0, "b.pdf")]
    assert dias["fecha"].to_list() == [None, True]


def test_saldos_diferentes_no_mesmo_dia_ficam_com_o_do_arquivo_dos_lancamentos_e_o_dia_e_marcado():
    # B, emitido depois, traz 04/06 só como saldo anterior, e diferente: fica o de A, que tem os lançamentos.
    outro = [B[0].replace("04/06/2026 1.240,00", "04/06/2026 1.239,00")]
    _, dias = _juntar(("a.pdf", A), ("b.pdf", outro))
    assert dias.select("dia", "arquivo", "saldo_do_dia", "divergente").rows() == [
        (_d("2026-05-31"), "a.pdf", 1050.0, False), (_d("2026-06-02"), "a.pdf", 1000.0, False),
        (_d("2026-06-04"), "a.pdf", 1240.0, True), (_d("2026-06-05"), "b.pdf", 1230.0, False),
        (_d("2026-06-08"), "b.pdf", 1195.0, False)]
    assert dias.filter(pl.col("dia") == _d("2026-06-04"))["fecha"].to_list() == [True]


def test_saldos_diferentes_num_dia_sem_lancamentos_ficam_com_o_emitido_mais_tarde():
    a = _lista("03/06/2026 1.000,00", emitido="05/06/2026 10:00:00")
    b = _lista("03/06/2026 999,00", emitido="06/06/2026 10:00:00")
    _, dias = _juntar(("z-velho.pdf", a), ("a-novo.pdf", b))
    assert dias.select("arquivo", "saldo_do_dia", "divergente").rows() == [("a-novo.pdf", 999.0, True)]


def test_arquivo_mais_novo_vence_o_mais_velho_com_mais_lancamentos_no_dia():
    # O banco tirou um lançamento de 04/06 depois de emitir o velho: o novo traz um só, e é ele que fica.
    novo = [A[0].replace("05/06/2026 10:00:00", "06/06/2026 10:00:00")
            .replace("04/06/2026 COMPRA DÉBITO MERCADO FICTICIO -10,00\n", "")]
    extrato, _ = _juntar(("velho.pdf", A), ("novo.pdf", novo))
    assert extrato.filter(pl.col("dia") == _d("2026-06-04")).select("arquivo", "descricao").rows() == [
        ("novo.pdf", "PIX RECEBIDO CICLANO FICTICIO")]


def test_o_mesmo_arquivo_duas_vezes_com_nomes_diferentes_nao_duplica():
    extrato, dias = _juntar(("a.pdf", A), ("copia-de-a.pdf", A))
    assert extrato.height == 4
    assert dias.height == 3
    assert set(extrato["arquivo"]) == {"copia-de-a.pdf"}   # empate: o nome maior, sempre o mesmo


def test_dia_igual_em_dois_arquivos_fica_com_o_emitido_mais_tarde():
    novo = [A[0].replace("05/06/2026 10:00:00", "06/06/2026 10:00:00").replace("MERCADO FICTICIO", "MERCADO NOVO")]
    extrato, _ = _juntar(("z-velho.pdf", A), ("a-novo.pdf", novo))
    assert "COMPRA DÉBITO MERCADO NOVO" in extrato["descricao"].to_list()
    assert "COMPRA DÉBITO MERCADO FICTICIO" not in extrato["descricao"].to_list()


def test_contas_diferentes_nao_se_misturam():
    lido = ler(A)
    lanc = pl.concat([lido.lancamentos.with_columns(**_origem(lido, "a.pdf")),
                      lido.lancamentos.with_columns(**_origem(lido, "outra.pdf", "p-bank-2"))])
    sald = pl.concat([lido.saldos.with_columns(**_origem(lido, "a.pdf")),
                      lido.saldos.with_columns(**_origem(lido, "outra.pdf", "p-bank-2"))])
    extrato, dias = juntar(lanc, sald)
    assert extrato.group_by("account_id").len().sort("account_id").rows() == [(BANK, 4), ("p-bank-2", 4)]
    assert dias.filter(pl.col("fecha").is_not_null())["fecha"].all()


def test_sem_arquivo_nenhum_as_tabelas_saem_vazias_e_tipadas():
    vazio = {"arquivo": pl.String, "emitido_em": pl.Datetime("us"), "account_id": pl.String}
    extrato, dias = juntar(pl.DataFrame(schema={**vazio, **LANCAMENTOS}), pl.DataFrame(schema={**vazio, **SALDOS}))
    assert (extrato.height, dias.height) == (0, 0)
    assert extrato.schema == pl.Schema(COLUNAS)
    assert dias.schema == pl.Schema(COLUNAS_SALDOS)


# --- o upload pelo app (df-7xk9.2) ----------------------------------------------------------------

def test_periodo_vai_do_saldo_anterior_ao_ultimo_lancamento():
    assert periodo(ler(A)) == (_d("2026-05-31"), _d("2026-06-05"))
    assert periodo(ler(_lista("03/06/2026 100,00"))) == (_d("2026-06-03"), _d("2026-06-03"))


def test_periodo_de_extrato_sem_lancamento_nem_saldo_para():
    vazio = ler(_lista())
    with pytest.raises(LayoutDesconhecido, match="sem lançamentos nem saldos"):
        periodo(vazio)


@pytest.mark.parametrize(("nome", "esperado"), [
    ("extrato_2026-05-31_2026-06-05.pdf", (_d("2026-05-31"), _d("2026-06-05"))),
    ("extrato_2026-05-31_2026-06-05-3.pdf", (_d("2026-05-31"), _d("2026-06-05"))),
    ("EXTRATO_2026-05-31_2026-06-05.PDF", (_d("2026-05-31"), _d("2026-06-05"))),
    ("extrato_2026-13-01_2026-06-05.pdf", None),
    ("extrato_2026-05-31_2026-06-05.pdf.parcial", None),
    ("Extrato conta corrente.pdf", None),
])
def test_periodo_do_nome(nome, esperado):
    assert periodo_do_nome(nome) == esperado


@pytest.mark.parametrize(("ocupados", "esperado"), [
    (set(), "extrato_2026-05-31_2026-06-05.pdf"),
    ({"a.pdf", "extrato_2026-05-31_2026-06-06.pdf"}, "extrato_2026-05-31_2026-06-05.pdf"),
    ({"extrato_2026-05-31_2026-06-05.pdf"}, "extrato_2026-05-31_2026-06-05-2.pdf"),
    ({"EXTRATO_2026-05-31_2026-06-05.PDF", "extrato_2026-05-31_2026-06-05-2.pdf"},
     "extrato_2026-05-31_2026-06-05-3.pdf"),
])
def test_nome_guardado_pelo_periodo_com_sufixo_se_ocupado(ocupados, esperado):
    nome = nome_guardado(_d("2026-05-31"), _d("2026-06-05"), ocupados)
    assert nome == esperado
    assert periodo_do_nome(nome) == (_d("2026-05-31"), _d("2026-06-05"))


def test_dias_divergentes_compara_arredondado_a_dois_e_so_os_dias_guardados():
    novos = pl.DataFrame({"dia": [_d("2026-06-02"), _d("2026-06-03"), _d("2026-06-04"), _d("2026-06-05")],
                          "saldo": [1000.004, 990.0, 1240.0, 7.0]}, schema=SALDOS)
    guardados = pl.DataFrame({"dia": [_d("2026-06-02"), _d("2026-06-03"), _d("2026-06-04")],
                              "saldo_do_dia": [1000.0, 991.0, 1240.0]})
    assert dias_divergentes(novos, guardados) == [_d("2026-06-03")]
    assert dias_divergentes(novos, guardados.clear()) == []


def test_guardados_traz_o_periodo_pelo_nome_ou_pelos_dias_guardados():
    emitido = datetime(2026, 6, 5, 10, 0, 0)
    arquivos = pl.DataFrame({"arquivo": ["extrato_2026-05-31_2026-06-05.pdf", "manual.pdf", "sem-dias.pdf"],
                             "account_uuid": ["c1", "c1", "c1"], "emitido_em": [emitido] * 3,
                             "n_lancamentos": [4, 2, 0], "n_saldos": [3, 1, 0], "linhas_ignoradas": [0, 1, 0]})
    lancamentos = pl.DataFrame({"arquivo": ["extrato_2026-05-31_2026-06-05.pdf", "manual.pdf", "manual.pdf"],
                                "dia": [_d("2026-06-04"), _d("2026-06-07"), _d("2026-06-08")]})
    saldos = pl.DataFrame({"arquivo": ["manual.pdf"], "dia": [_d("2026-06-06")]})
    assert guardados(arquivos, lancamentos, saldos) == [
        {"arquivo": "extrato_2026-05-31_2026-06-05.pdf", "periodo": {"de": _d("2026-05-31"), "ate": _d("2026-06-05")},
         "emitido_em": emitido, "n_lancamentos": 4, "n_saldos": 3, "linhas_ignoradas": 0},
        {"arquivo": "manual.pdf", "periodo": {"de": _d("2026-06-06"), "ate": _d("2026-06-08")},
         "emitido_em": emitido, "n_lancamentos": 2, "n_saldos": 1, "linhas_ignoradas": 1},
        {"arquivo": "sem-dias.pdf", "periodo": {"de": None, "ate": None},
         "emitido_em": emitido, "n_lancamentos": 0, "n_saldos": 0, "linhas_ignoradas": 0},
    ]
    assert guardados(arquivos.clear()) == []


# --- a task ---------------------------------------------------------------------------------------

def _texto_pdf(texto):
    return texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("cp1252")


def _pdf(paginas):
    """Um PDF de texto puro, uma linha de texto por linha da página (Helvetica, WinAnsi)."""
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    folhas = []
    for texto in paginas:
        conteudo = b"BT /F1 8 Tf 10 TL 30 800 Td " + b" ".join(
            b"(" + _texto_pdf(linha) + b") Tj T*" for linha in texto.splitlines()) + b" ET"
        objetos.append(b"<< /Length %d >>\nstream\n" % len(conteudo) + conteudo + b"\nendstream")
        objetos.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >>"
                       b" /Contents %d 0 R >>" % len(objetos))
        folhas.append(len(objetos))
    objetos[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % f for f in folhas), len(folhas))
    saida, posicoes = bytearray(b"%PDF-1.4\n"), []
    for numero, corpo in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n" % numero + corpo + b"\nendobj\n"
    xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    saida += b"".join(b"%010d 00000 n \n" % p for p in posicoes)
    saida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, xref)
    return bytes(saida)


def _workspace(tmp_path, extratos=None, transfer_number="901/0001/0001234567-8"):
    ws = tmp_path / "ws"
    data = ws / "data"
    _delta_util.seed(data, "cleansed", "accounts", pl.DataFrame(
        {"id": [BANK, "p-card-1"], "type": ["BANK", "CREDIT"], "number": ["0001234567-8", "1234"],
         "transfer_number": [transfer_number, None]}))
    _delta_util.seed(data, "curated", "dim_account", pl.DataFrame(
        {"pluggy_account_id": [BANK, "p-card-1"], "account_uuid": ["banco-checking-3456", "banco-credit-1234"]}))
    if extratos is not None:
        (data / "extratos").mkdir()
        for nome, paginas in extratos.items():
            (data / "extratos" / nome).write_bytes(_pdf(paginas))
    return ws


class _Saida(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


@pytest.fixture
def rodar(capsys, monkeypatch):
    """Roda a task no processo do teste (a cobertura vê o script), com `pypdf` no lugar do pacote: o real, um
    de mentira ou None (o import falha). Devolve o código de saída e o que ela imprimiu, como um processo: um
    SystemExit com texto sai 1 com o texto no stderr."""
    def _rodar(ws, pypdf=pypdf):
        monkeypatch.setattr(sys, "path", list(sys.path))   # o script põe a pasta report/ no sys.path
        monkeypatch.setitem(sys.modules, "pypdf", pypdf)
        ns = _delta_util.run_script("cleansed2curated", "cleansed2curated_extrato",
                                    ["--source", "extratos", "--target", "curated"], ws / "data")
        saida = capsys.readouterr()
        codigo = getattr(ns.get("_system_exit"), "code", None)
        if isinstance(codigo, str):
            return _Saida(1, saida.out, saida.err + codigo + "\n")
        return _Saida(codigo or 0, saida.out, saida.err)
    return _rodar


def _rodar_processo(ws):
    # Só este roda a task como processo, porque o que ele confere não existe no processo do teste: o
    # ambiente que o engine dá à task na prod (dataforge/tasks/python_handler.py), sem PYTHONIOENCODING, em que
    # o script imprime na codificação do locale (cp1252 no Windows) e text=True a lê de volta na mesma. O CI
    # põe PYTHONIOENCODING=utf-8 para o seu relatório; herdado, o script imprimiria UTF-8 que text=True leria
    # errado.
    for arquivo in ("cleansed2curated/cleansed2curated_extrato.py", "functions/functions.py", "report/extrato.py"):
        (ws / "scripts" / arquivo).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_delta_util.SCRIPTS / arquivo, ws / "scripts" / arquivo)
    (ws / "connections").mkdir()
    shutil.copy(_delta_util.ROOT / "connections" / "connections.yaml", ws / "connections" / "connections.yaml")
    base = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    return subprocess.run(
        [sys.executable, str(ws / "scripts" / "cleansed2curated" / "cleansed2curated_extrato.py"),
         "--source", "extratos", "--target", "curated"],
        capture_output=True, text=True, cwd=ws, check=False, env=base)


def _cifrado(paginas):
    escritor = pypdf.PdfWriter(clone_from=io.BytesIO(_pdf(paginas)))
    escritor.encrypt(user_password="senha-ficticia", algorithm="RC4-128")
    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def _tabela(ws, nome):
    return _delta_util.read(ws / "data", "curated", nome)


def test_o_pdf_feito_aqui_tem_o_texto_que_o_banco_poe():
    paginas = [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(_pdf(A + B))).pages]
    assert [linha.strip() for linha in paginas[1].splitlines()] == B[0].splitlines()


def test_task_no_processo_do_engine_imprime_na_codificacao_do_locale(tmp_path):
    ws = _workspace(tmp_path, {"a.pdf": A})
    result = _rodar_processo(ws)
    assert result.returncode == 0, result.stderr
    assert "extrato: 1 arquivo(s) lido(s), 4 lançamentos, 3 saldos do dia, 0 dia(s) que não fecham" in result.stdout


def test_task_grava_extrato_saldos_e_arquivos_por_account_uuid(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": A, "b.PDF": B})
    (ws / "data" / "extratos" / "leia-me.txt").write_text("não é PDF", encoding="utf-8")
    result = rodar(ws)
    assert result.returncode == 0, result.stderr
    extrato, dias, arquivos = (_tabela(ws, t) for t in ("extrato", "extrato_saldos", "extrato_arquivos"))
    assert extrato.columns == ["arquivo", "account_uuid", "dia", "ordem", "descricao", "valor", "saldo_do_dia"]
    assert set(extrato["account_uuid"]) == set(dias["account_uuid"]) == {"banco-checking-3456"}
    assert (extrato.height, dias.height) == (6, 5)
    assert arquivos.select("arquivo", "account_uuid", "n_lancamentos", "n_saldos", "linhas_ignoradas").rows() == [
        ("a.pdf", "banco-checking-3456", 4, 3, 0), ("b.PDF", "banco-checking-3456", 3, 3, 0)]
    assert "extrato: 2 arquivo(s) lido(s), 6 lançamentos, 5 saldos do dia, 0 dia(s) que não fecham" in result.stdout
    for tabela in ("curated.extrato", "curated.extrato_saldos", "curated.extrato_arquivos"):
        assert json.loads((ws / "data" / "validation" / f"{tabela}.json").read_text())["findings"] == []


def test_task_imprime_e_grava_as_linhas_ignoradas_sem_achado(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": [A[0].replace("02/06/2026 1.000,00", "UM TITULO NO MEIO\n02/06/2026 1.000,00")]})
    result = rodar(ws)
    assert result.returncode == 0, result.stderr
    assert ("extrato: arquivo 1 de 1 (2026-05-31 a 2026-06-05): 4 lançamentos, 3 saldos, 1 linha(s) ignorada(s)"
            in result.stdout)
    assert "a.pdf" not in result.stdout
    assert _tabela(ws, "extrato_arquivos")["linhas_ignoradas"].to_list() == [1]
    assert json.loads((ws / "data" / "validation" / "curated.extrato_arquivos.json").read_text())["findings"] == []


def test_task_imprime_os_dias_que_nao_fecham_sem_valores(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": [A[0].replace("04/06/2026 1.240,00", "04/06/2026 1.234,00")]})
    result = rodar(ws)
    assert result.returncode == 0, result.stderr
    assert "1 dia(s) que não fecham 2026-06-04" in result.stdout
    assert not any(v in result.stdout for v in ("1.234", "1234", "FICTICIA", "banco-checking", "12345"))


@pytest.mark.parametrize("extratos", [None, {}], ids=["pasta-ausente", "pasta-vazia"])
@pytest.mark.parametrize("sem_pypdf", [False, True], ids=["com-pypdf", "sem-pypdf"])
def test_pasta_vazia_ou_ausente_grava_tabelas_vazias_e_nao_falha(tmp_path, rodar, extratos, sem_pypdf):
    ws = _workspace(tmp_path, extratos)
    result = rodar(ws, None if sem_pypdf else pypdf)
    assert result.returncode == 0, result.stderr
    for tabela in ("extrato", "extrato_saldos", "extrato_arquivos"):
        assert _tabela(ws, tabela).height == 0
    assert "extrato: 0 arquivo(s) lido(s), 0 lançamentos, 0 saldos do dia, 0 dia(s) que não fecham" in result.stdout
    assert "AVISO" not in result.stdout
    assert (ws / "data" / "extratos").exists() == (extratos is not None)   # a task só lê a pasta


def test_pdf_na_pasta_sem_pypdf_avisa_nao_mexe_nas_tabelas_e_nao_falha(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": A})
    assert rodar(ws).returncode == 0
    antes = {tabela: _tabela(ws, tabela) for tabela in ("extrato", "extrato_saldos", "extrato_arquivos")}
    assert antes["extrato"].height == 4
    result = rodar(ws, None)
    assert result.returncode == 0, result.stderr
    assert "AVISO: 1 PDF(s) na pasta, mas a biblioteca pypdf não está instalada" in result.stdout
    for tabela, quadro in antes.items():
        assert _tabela(ws, tabela).equals(quadro), tabela


def test_pdf_na_pasta_sem_pypdf_e_sem_tabelas_nao_grava_nada(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": A})
    result = rodar(ws, None)
    assert result.returncode == 0, result.stderr
    assert not (ws / "data" / "curated" / "extrato").exists()


_ERROS = {"OSError": OSError, "KeyError": KeyError, "NotImplementedError": NotImplementedError,
          "zlib.error": zlib.error, "RuntimeError": RuntimeError}


class _PaginaDeMentira:
    def __init__(self, texto):
        self.texto = texto

    def extract_text(self):
        return self.texto


class _LeitorDeMentira:
    # Lê o arquivo como texto (as páginas separadas por \f); quando o texto é o nome de um erro, levanta esse
    # erro com o caminho do arquivo na mensagem.
    def __init__(self, caminho):
        texto = caminho.read_text(encoding="utf-8")
        if texto in _ERROS:
            raise _ERROS[texto](f"falha de mentira em {caminho}")
        self.pages = [_PaginaDeMentira(p) for p in texto.split("\f")]


def _pypdf_de_mentira():
    modulo = types.ModuleType("pypdf")
    modulo.PdfReader = _LeitorDeMentira
    return modulo


@pytest.mark.parametrize("erro", ["OSError", "KeyError", "NotImplementedError", "zlib.error", "RuntimeError"])
def test_qualquer_erro_lendo_um_pdf_isola_o_arquivo_e_o_nomeia(tmp_path, rodar, erro):
    ws = _workspace(tmp_path, {})
    (ws / "data" / "extratos" / "a.pdf").write_text("\f".join(A), encoding="utf-8")
    (ws / "data" / "extratos" / "conta-ficticia-99.pdf").write_text(erro, encoding="utf-8")
    result = rodar(ws, _pypdf_de_mentira())
    assert result.returncode != 0
    assert "extrato: 1 arquivo(s) ruim(ns) ficaram de fora: arquivo 2 de 2: não deu para ler: " in result.stderr
    assert erro.split(".")[-1] in result.stderr
    assert "conta-ficticia-99" not in result.stderr + result.stdout
    assert _tabela(ws, "extrato_arquivos")["arquivo"].to_list() == ["a.pdf"]


def test_arquivos_ruins_ficam_de_fora_os_bons_sao_gravados_e_a_task_falha_nomeando_todos(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": A, "cortado.pdf": [A[0].replace(RODAPE, "")],
                               "data-ruim.pdf": [A[0].replace("02/06/2026 1.000,00", "31/02/2026 1.000,00")],
                               "outra-conta.pdf": [A[0].replace("conta: 1234567-8", "conta: 9999999-0")]})
    (ws / "data" / "extratos" / "quebrado.pdf").write_bytes(b"isto nao e um pdf")
    (ws / "data" / "extratos" / "cifrado.pdf").write_bytes(_cifrado(A))
    result = rodar(ws)
    assert result.returncode != 0
    assert "extrato: 5 arquivo(s) ruim(ns) ficaram de fora" in result.stderr
    # Pela ordem do nome: a, cifrado, cortado, data-ruim, outra-conta, quebrado.
    motivos = dict(parte.split(": ", 1) for parte in result.stderr.split("ficaram de fora: ", 1)[1].strip().split("; "))
    assert list(motivos) == [f"arquivo {posicao} de 6" for posicao in range(2, 7)]
    for posicao, motivo in ((2, "não deu para ler"), (3, "que fecha a lista"), (4, "data impossível 31/02/2026"),
                            (5, "casam com 0 contas BANK"), (6, "não deu para ler")):
        assert motivo in motivos[f"arquivo {posicao} de 6"], posicao
    for nome in ("cifrado.pdf", "cortado.pdf", "data-ruim.pdf", "outra-conta.pdf", "quebrado.pdf"):
        assert nome not in result.stderr + result.stdout, nome
    assert set(_tabela(ws, "extrato")["arquivo"]) == {"a.pdf"}
    assert _tabela(ws, "extrato_arquivos")["arquivo"].to_list() == ["a.pdf"]
    assert "extrato: 1 arquivo(s) lido(s), 4 lançamentos" in result.stdout


def test_saldos_diferentes_no_mesmo_dia_nao_param_a_task_e_o_dia_sai_marcado(tmp_path, rodar):
    ws = _workspace(tmp_path, {"a.pdf": A, "b.pdf": [B[0].replace("04/06/2026 1.240,00", "04/06/2026 1.239,00")]})
    result = rodar(ws)
    assert result.returncode == 0, result.stderr
    assert "AVISO: 1 dia(s) com saldos diferentes em arquivos diferentes" in result.stdout
    assert result.stdout.rstrip().splitlines()[-2].endswith(": 2026-06-04")
    assert not any(v in result.stdout for v in ("a.pdf", "b.pdf", "1.239", "1239"))
    dias = _tabela(ws, "extrato_saldos")
    assert dias.filter(pl.col("divergente"))["dia"].to_list() == [_d("2026-06-04")]
    assert _tabela(ws, "extrato").height == 6
    assert json.loads((ws / "data" / "validation" / "curated.extrato_saldos.json").read_text())["findings"] == []
