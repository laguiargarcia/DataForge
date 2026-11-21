"""Conferência do cartão pela fatura (df-acz4): por fatura fechada de curated.fact_card_statement, o total contra
os lançamentos do cartão dela e contra os pagamentos de fatura lançados perto do vencimento.

A função pura (scripts/report/conferencia_fatura.py) e a task que grava curated.conferencia_fatura.
Dados falsos: nenhum id, nome ou valor vem do dado real.
"""
import json
import sys
from datetime import date, timedelta

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from conferencia_fatura import (A_VENCER, CATEGORIAS_PAGAMENTO, COLUNAS, COPIAS_COLUNAS, FECHA,
                                HISTORICO_INCOMPLETO, JANELA_PAGAMENTO_DIAS, MAX_COMBINACOES, NAO_FECHA,
                                POR_FATURA_COLUNAS, SEM_TOTAL, TOLERANCIA, conferencia_fatura, copias_pela_fatura)

CARTAO, OUTRO = "cartao-0001", "cartao-0002"
COMPRA, ESTORNO = "09000000", "16030000"
PAGAMENTO, PAGAMENTO_DESDE_JULHO = CATEGORIAS_PAGAMENTO
HOJE = date(2026, 6, 30)

_FATURAS_SCHEMA = {"account_uuid": pl.Utf8, "statement_id": pl.Utf8, "due_date": pl.Date,
                   "statement_amount": pl.Float64}
_BILLS_SCHEMA = {"id": pl.Utf8, "account_uuid": pl.Utf8, "due_date": pl.Date}
_LANC_SCHEMA = {"account_uuid": pl.Utf8, "date": pl.Date, "amount": pl.Float64, "categoryId": pl.Utf8,
                "cc_bill_id": pl.Utf8}


def _d(text):
    return date.fromisoformat(text)


def _faturas(*rows):
    # (statement_id, due_date, total[, account_uuid])
    return pl.DataFrame([(r[3] if len(r) > 3 else CARTAO, r[0], _d(r[1]), r[2]) for r in rows],
                        schema=_FATURAS_SCHEMA, orient="row")


def _bills(*rows):
    # (id, due_date[, account_uuid]): cleansed.bills, gêmeos reemitidos incluídos.
    return pl.DataFrame([(r[0], r[2] if len(r) > 2 else CARTAO, _d(r[1])) for r in rows],
                        schema=_BILLS_SCHEMA, orient="row")


def _lanc(*rows):
    # (date, amount, categoryId, cc_bill_id[, account_uuid])
    return pl.DataFrame([(r[4] if len(r) > 4 else CARTAO, _d(r[0]), r[1], r[2], r[3]) for r in rows],
                        schema=_LANC_SCHEMA, orient="row")


def _conferir(faturas, lancamentos, bills=None, hoje=HOJE):
    if bills is None:
        bills = faturas.select("statement_id", "account_uuid", "due_date").rename({"statement_id": "id"})
    return conferencia_fatura(faturas, bills, lancamentos, hoje)


def _por_fatura(out, *cols):
    return {r["statement_id"]: tuple(r[c] for c in cols) for r in out.iter_rows(named=True)}


# Duas faturas pagas no vencimento; o histórico começa em 01/03, antes do ciclo da primeira.
FATURAS = _faturas(("f4", "2026-04-17", 150.0), ("f5", "2026-05-17", 80.0))
LANCAMENTOS = (
    ("2026-03-01", 10.0, COMPRA, "f4"), ("2026-03-20", 140.0, COMPRA, "f4"),
    ("2026-04-17", -150.0, PAGAMENTO, "f5"),           # pagamento da f4, com o billId da fatura seguinte
    ("2026-04-20", 100.0, COMPRA, "f5"), ("2026-04-25", -20.0, ESTORNO, "f5"),
    ("2026-05-18", -80.0, PAGAMENTO_DESDE_JULHO, None),    # pagamento da f5, sem billId
)


def test_fatura_que_fecha_nas_duas_contas():
    out = _conferir(FATURAS, _lanc(*LANCAMENTOS))
    assert out.schema == pl.Schema(COLUNAS)
    assert _por_fatura(out, "soma_lancamentos", "n_lancamentos", "diferenca_lancamentos", "estado_lancamentos",
                       "pago", "n_pagamentos", "diferenca_pagamento", "estado_pagamento") == {
        "f4": (150.0, 2, 0.0, FECHA, 150.0, 1, 0.0, FECHA),
        "f5": (80.0, 2, 0.0, FECHA, 80.0, 1, 0.0, FECHA)}


def test_compra_copiada_aparece_na_diferenca_dos_lancamentos():
    # A mesma compra sob dois ids (df-mk25): sobra o valor dela, e o pagamento continua fechando.
    out = _conferir(FATURAS, _lanc(*LANCAMENTOS, ("2026-03-20", 140.0, COMPRA, "f4")))
    assert _por_fatura(out, "diferenca_lancamentos", "estado_lancamentos", "estado_pagamento")["f4"] == (
        -140.0, NAO_FECHA, FECHA)


def test_lancamento_que_falta_aparece_positivo():
    out = _conferir(FATURAS, _lanc(*[r for r in LANCAMENTOS if r[1] != 140.0]))
    assert _por_fatura(out, "diferenca_lancamentos", "estado_lancamentos")["f4"] == (140.0, NAO_FECHA)


def test_pagamento_copiado_aparece_na_diferenca_do_pagamento():
    # O pagamento sob dois ids (df-75ol): pago em dobro, e os lançamentos continuam fechando.
    out = _conferir(FATURAS, _lanc(*LANCAMENTOS, ("2026-05-18", -80.0, PAGAMENTO, "f5")))
    assert _por_fatura(out, "pago", "n_pagamentos", "diferenca_pagamento", "estado_pagamento",
                       "estado_lancamentos")["f5"] == (160.0, 2, -80.0, NAO_FECHA, FECHA)


def test_pagamento_conta_pela_data_nao_pelo_billid():
    # O billId do pagamento aponta para a própria fatura, para a seguinte ou para nenhuma: a data decide.
    for bill in ("f4", "f5", None):
        out = _conferir(FATURAS, _lanc(*[r for r in LANCAMENTOS if r[1] != -150.0],
                                       ("2026-04-19", -150.0, PAGAMENTO, bill)))
        assert _por_fatura(out, "pago", "diferenca_lancamentos") == {"f4": (150.0, 0.0), "f5": (80.0, 0.0)}


def test_credito_que_nao_e_pagamento_entra_nos_lancamentos():
    # Um crédito negativo de outra categoria (estorno) desconta da fatura; um lançamento positivo ou zero com
    # categoria de pagamento continua sendo lançamento.
    out = _conferir(_faturas(("f4", "2026-04-17", 25.0)),
                    _lanc(("2026-03-01", 40.0, COMPRA, "f4"), ("2026-03-02", -20.0, ESTORNO, "f4"),
                          ("2026-03-03", 5.0, PAGAMENTO, "f4"), ("2026-03-04", 0.0, PAGAMENTO, "f4"),
                          ("2026-04-17", -25.0, PAGAMENTO, None)))
    assert _por_fatura(out, "soma_lancamentos", "n_lancamentos", "estado_lancamentos", "pago", "n_pagamentos")[
        "f4"] == (25.0, 4, FECHA, 25.0, 1)


def test_somas_saem_arredondadas_no_centavo():
    out = _conferir(_faturas(("f4", "2026-04-17", 0.3)),
                    _lanc(("2026-03-01", 0.1, COMPRA, "f4"), ("2026-03-02", 0.2, COMPRA, "f4"),
                          ("2026-04-17", -0.1, PAGAMENTO, None), ("2026-04-18", -0.2, PAGAMENTO, None)))
    assert _por_fatura(out, "soma_lancamentos", "pago")["f4"] == (0.3, 0.3)


def test_fatura_nao_paga_vira_saldo_anterior_da_seguinte():
    # A f4 não foi paga: a f5 traz o saldo dela e fecha; a f4 não fecha no pagamento.
    # As faturas vêm fora de ordem: a anterior é a do vencimento anterior.
    faturas = _faturas(("f6", "2026-06-17", 0.0), ("f4", "2026-04-17", 30.0), ("f5", "2026-05-17", 30.0))
    out = _conferir(faturas, _lanc(("2026-03-01", 30.0, COMPRA, "f4"), ("2026-06-10", -30.0, ESTORNO, "f6")))
    assert _por_fatura(out, "saldo_anterior", "diferenca_lancamentos", "estado_lancamentos",
                       "diferenca_pagamento", "estado_pagamento") == {
        "f4": (0.0, 0.0, FECHA, 30.0, NAO_FECHA), "f5": (30.0, 0.0, FECHA, 30.0, NAO_FECHA),
        "f6": (30.0, 0.0, FECHA, 0.0, FECHA)}


def test_saldo_anterior_e_do_mesmo_cartao():
    # A última fatura do primeiro cartão ficou sem pagar; a primeira do outro cartão não herda o saldo dela.
    faturas = pl.concat([_faturas(("f4", "2026-04-17", 30.0)), _faturas(("g5", "2026-05-17", 20.0, OUTRO))])
    out = _conferir(faturas, _lanc(("2026-03-01", 30.0, COMPRA, "f4"),
                                   ("2026-04-01", 20.0, COMPRA, "g5", OUTRO)))
    assert _por_fatura(out, "saldo_anterior", "estado_lancamentos") == {"f4": (0.0, FECHA), "g5": (0.0, FECHA)}


def test_pagamento_a_mais_nao_vira_saldo_anterior_negativo():
    # Um pagamento copiado só aparece na coluna do pagamento, não também na fatura seguinte.
    out = _conferir(FATURAS, _lanc(*LANCAMENTOS, ("2026-04-17", -150.0, PAGAMENTO, "f5")))
    assert _por_fatura(out, "saldo_anterior", "estado_lancamentos", "estado_pagamento") == {
        "f4": (0.0, FECHA, NAO_FECHA), "f5": (0.0, FECHA, FECHA)}


def test_fatura_anterior_ao_historico_e_historico_incompleto():
    # O histórico começa em 10/03: o ciclo da f4 começa antes (40 dias antes do vencimento), o da f5 não.
    faturas = _faturas(("f4", "2026-04-17", 150.0), ("f5", "2026-05-17", 50.0))
    lanc = _lanc(("2026-03-10", 100.0, COMPRA, "f4"), ("2026-04-17", -150.0, PAGAMENTO, None),
                 ("2026-04-20", 40.0, COMPRA, "f5"), ("2026-05-17", -50.0, PAGAMENTO, None))
    out = _conferir(faturas, lanc)
    assert _por_fatura(out, "diferenca_lancamentos", "estado_lancamentos") == {
        "f4": (50.0, HISTORICO_INCOMPLETO), "f5": (10.0, NAO_FECHA)}


def test_ciclo_comeca_no_dia_do_inicio_do_historico_e_completo():
    # O ciclo começa DIAS_DO_CICLO (40) dias antes do vencimento: o histórico que começa nesse dia o cobre, o
    # que começa no dia seguinte não.
    faturas = _faturas(("f4", "2026-04-17", 50.0))
    for inicio, estado in (("2026-03-08", NAO_FECHA), ("2026-03-09", HISTORICO_INCOMPLETO)):
        out = _conferir(faturas, _lanc((inicio, 10.0, COMPRA, "f4")))
        assert _por_fatura(out, "estado_lancamentos")["f4"] == (estado,), inicio


def test_pagamento_anterior_ao_historico_e_historico_incompleto():
    out = _conferir(_faturas(("f4", "2026-04-17", 15.0)), _lanc(("2026-04-12", 15.0, COMPRA, "f4")))
    assert _por_fatura(out, "estado_lancamentos", "estado_pagamento")["f4"] == (FECHA, HISTORICO_INCOMPLETO)


def test_conta_sem_lancamento_nenhum_e_historico_incompleto():
    out = _conferir(_faturas(("f4", "2026-04-17", 15.0)), _lanc())
    assert _por_fatura(out, "estado_lancamentos", "estado_pagamento")["f4"] == (
        HISTORICO_INCOMPLETO, HISTORICO_INCOMPLETO)


def test_fatura_ainda_a_vencer():
    # Sem pagamento, a f5 está a_vencer até JANELA_PAGAMENTO_DIAS depois do vencimento; depois, nao_fecha.
    lanc = _lanc(*LANCAMENTOS[:-1])
    fim = _d("2026-05-17") + timedelta(days=JANELA_PAGAMENTO_DIAS)
    assert _por_fatura(_conferir(FATURAS, lanc, hoje=fim), "estado_pagamento")["f5"] == (A_VENCER,)
    assert _por_fatura(_conferir(FATURAS, lanc, hoje=fim + timedelta(days=1)), "estado_pagamento")["f5"] == (
        NAO_FECHA,)


def test_pagamento_fora_da_janela_nao_conta_para_fatura_nenhuma():
    # A janela vai até JANELA_PAGAMENTO_DIAS (10) dias do vencimento, dos dois lados.
    for dia, pago in (("2026-05-27", 80.0), ("2026-05-28", 0.0), ("2026-05-07", 80.0), ("2026-05-06", 0.0)):
        out = _conferir(FATURAS, _lanc(*LANCAMENTOS[:-1], (dia, -80.0, PAGAMENTO, None)))
        assert _por_fatura(out, "pago")["f5"] == (pago,), dia


def test_pagamento_adiantado_conta_para_a_fatura_que_vence_depois():
    # Pago 5 dias antes do vencimento da f5: é dela, não da f4, que venceu 25 dias antes.
    out = _conferir(FATURAS, _lanc(*LANCAMENTOS[:-1], ("2026-05-12", -80.0, PAGAMENTO, None)))
    assert _por_fatura(out, "pago") == {"f4": (150.0,), "f5": (80.0,)}


def test_pagamento_de_outro_cartao_nao_conta():
    faturas = pl.concat([FATURAS, _faturas(("g5", "2026-05-17", 80.0, OUTRO))])
    out = _conferir(faturas, _lanc(*LANCAMENTOS))
    assert _por_fatura(out, "pago", "estado_pagamento", "estado_lancamentos")["g5"] == (
        0.0, HISTORICO_INCOMPLETO, HISTORICO_INCOMPLETO)
    assert _por_fatura(out, "pago")["f5"] == (80.0,)


def test_lancamento_de_gemeo_reemitido_conta_para_a_fatura_que_ficou():
    # fact_card_statement deixou o gêmeo f5-velha de fora; um lançamento que ainda aponta para ele conta na f5.
    bills = _bills(("f4", "2026-04-17"), ("f5", "2026-05-17"), ("f5-velha", "2026-05-18"))
    lanc = _lanc(*[r for r in LANCAMENTOS if r[1] != 100.0], ("2026-04-20", 100.0, COMPRA, "f5-velha"))
    out = _conferir(FATURAS, lanc, bills=bills)
    assert _por_fatura(out, "n_lancamentos", "estado_lancamentos")["f5"] == (2, FECHA)


def test_lancamento_de_fatura_desconhecida_fica_de_fora():
    out = _conferir(FATURAS, _lanc(*LANCAMENTOS, ("2026-04-21", 7.0, COMPRA, "nenhuma")))
    assert _por_fatura(out, "n_lancamentos", "estado_lancamentos")["f5"] == (2, FECHA)


def test_fatura_sem_total():
    faturas = _faturas(("f4", "2026-04-17", None), ("f5", "2026-05-17", 80.0))
    out = _conferir(faturas, _lanc(*LANCAMENTOS))
    assert _por_fatura(out, "diferenca_lancamentos", "estado_lancamentos", "estado_pagamento",
                       "saldo_anterior") == {"f4": (None, SEM_TOTAL, SEM_TOTAL, 0.0), "f5": (0.0, FECHA, FECHA, 0.0)}


def test_sem_fatura_tabela_vazia():
    out = _conferir(_faturas(), _lanc(*LANCAMENTOS))
    assert out.is_empty()
    assert out.schema == pl.Schema(COLUNAS)


# Propriedade: faturas mensais cujas compras somam o total e que são pagas no vencimento fecham todas; uma
# compra copiada (acima da tolerância) muda só a fatura dela, e a diferença é menos o valor da cópia (o caso da
# df-mk25).
_centavos = st.integers(min_value=round(TOLERANCIA * 100) + 1, max_value=100_000)


@settings(max_examples=60, deadline=None)
@given(compras=st.lists(st.lists(_centavos, min_size=1, max_size=5), min_size=1, max_size=6), data=st.data())
def test_propriedade_compra_copiada_muda_so_a_sua_fatura(compras, data):
    faturas, lanc = [], [("2025-12-01", 0.01, COMPRA, "f0")]
    faturas.append(("f0", "2026-01-10", 0.01))
    lanc.append(("2026-01-10", -0.01, PAGAMENTO, None))
    for m, valores in enumerate(compras, start=1):
        due = date(2026, 1, 10) + timedelta(days=30 * m)
        total = round(sum(valores) / 100, 2)
        faturas.append((f"f{m}", due.isoformat(), total))
        lanc += [((due - timedelta(days=15)).isoformat(), v / 100, COMPRA, f"f{m}") for v in valores]
        lanc.append((due.isoformat(), -total, PAGAMENTO, f"f{m + 1}"))
    out = _conferir(_faturas(*faturas), _lanc(*lanc), hoje=date(2027, 1, 1))
    assert set(out["estado_lancamentos"]) == {FECHA}
    assert set(out["estado_pagamento"]) == {FECHA}

    m = data.draw(st.integers(min_value=1, max_value=len(compras)))
    copia = data.draw(st.sampled_from([r for r in lanc if r[3] == f"f{m}" and r[2] == COMPRA]))
    out = _conferir(_faturas(*faturas), _lanc(*lanc, copia), hoje=date(2027, 1, 1))
    assert _por_fatura(out.filter(pl.col("estado_lancamentos") != FECHA), "diferenca_lancamentos") == {
        f"f{m}": (-copia[1],)}
    assert set(out["estado_pagamento"]) == {FECHA}


# --- cópias que a fatura prova (df-mk25) ------------------------------------------------------------------------
# O Pluggy devolve um lançamento do cartão sob dois ids. Sai a cópia (o de createdAt mais recente) só quando a
# fatura sobra e uma única combinação de candidatos (mesma fatura, valor e tipo) soma a sobra.

_LANC_COPIA_SCHEMA = _LANC_SCHEMA | {"id": pl.Utf8, "txn_type": pl.Utf8, "createdAt": pl.Utf8}
CEDO, TARDE = "2026-03-20T10:00:00.000Z", "2026-05-02T08:30:00.000Z"


def _lanc_c(*rows):
    # (id, date, amount, categoryId, cc_bill_id, createdAt[, txn_type]): o tipo sai do sinal, como no cartão.
    return pl.DataFrame(
        [(CARTAO, _d(r[1]), r[2], r[3], r[4], r[0], r[6] if len(r) > 6 else ("DEBIT" if r[2] > 0 else "CREDIT"),
          r[5]) for r in rows], schema=_LANC_COPIA_SCHEMA, orient="row")


def _copias(faturas, lancamentos, bills=None):
    if bills is None:
        bills = faturas.select("statement_id", "account_uuid", "due_date").rename({"statement_id": "id"})
    copias, por_fatura = copias_pela_fatura(faturas, bills, lancamentos, HOJE)
    assert copias.schema == pl.Schema(COPIAS_COLUNAS)
    assert por_fatura.schema == pl.Schema(POR_FATURA_COLUNAS)
    return (sorted(copias.select("id", "mantido").rows()),
            {r["statement_id"]: (r["candidatos"], r["removidos"], r["retida"]) for r in por_fatura.to_dicts()})


F4 = _faturas(("f4", "2026-04-17", 150.0))
BASE = (("a", "2026-03-01", 10.0, COMPRA, "f4", CEDO), ("b", "2026-03-20", 140.0, COMPRA, "f4", CEDO))


def test_copia_que_a_fatura_prova_sai_e_fica_a_criada_primeiro():
    # A cópia vem com outra data e é a criada depois: o dia não entra na chave.
    assert _copias(F4, _lanc_c(*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE))) == (
        [("c", "b")], {"f4": (2, 1, False)})


def test_fica_a_criada_primeiro_tambem_quando_vem_depois_na_lista():
    rows = (BASE[0], ("c", "2026-03-20", 140.0, COMPRA, "f4", TARDE), BASE[1])
    assert _copias(F4, _lanc_c(*rows))[0] == [("c", "b")]


def test_createdat_e_comparado_como_instante_nao_como_texto():
    # Como texto, '...12:00:00Z' fica depois de '...12:00:00.500Z'; como instante, antes.
    rows = (BASE[0], ("b", "2026-03-20", 140.0, COMPRA, "f4", "2026-06-16T12:00:00.500Z"),
            ("c", "2026-03-20", 140.0, COMPRA, "f4", "2026-06-16T12:00:00Z"))
    assert _copias(F4, _lanc_c(*rows))[0] == [("b", "c")]


def test_compra_repetida_de_verdade_numa_fatura_que_fecha_fica():
    assert _copias(_faturas(("f4", "2026-04-17", 290.0)),
                   _lanc_c(*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE))) == ([], {})


def test_sobra_que_nao_e_de_copia_nao_tira_nada_e_nao_e_achado():
    # Sobram 145, os candidatos só explicam 140: a conferência mostra a fatura; nada sai.
    rows = (*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE), ("d", "2026-03-22", 5.0, COMPRA, "f4", CEDO))
    assert _copias(F4, _lanc_c(*rows)) == ([], {"f4": (2, 0, False)})


def test_mais_de_uma_combinacao_retem_a_fatura():
    # Sobram 60: duas cópias de 30 ou uma de 60. Nada sai.
    rows = (("a", "2026-03-01", 30.0, COMPRA, "f4", CEDO), ("b", "2026-03-02", 30.0, COMPRA, "f4", TARDE),
            ("c", "2026-03-03", 30.0, COMPRA, "f4", "2026-07-01T00:00:00.000Z"),
            ("d", "2026-03-04", 60.0, COMPRA, "f4", CEDO), ("e", "2026-03-05", 60.0, COMPRA, "f4", TARDE))
    assert _copias(_faturas(("f4", "2026-04-17", 150.0)), _lanc_c(*rows)) == ([], {"f4": (5, 0, True)})


def test_combinacoes_demais_retem_a_fatura():
    # Grupos de 2 lançamentos: 2 ** 13 combinações passa de MAX_COMBINACOES, nada sai.
    n = (MAX_COMBINACOES - 1).bit_length() + 1
    rows = [(f"{k}{i}", "2026-03-01", float(k + 1), COMPRA, "f4", (CEDO, TARDE)[i]) for k in range(n) for i in (0, 1)]
    total = sum(k + 1 for k in range(n))
    assert _copias(_faturas(("f4", "2026-04-17", float(total))), _lanc_c(*rows)) == ([], {"f4": (2 * n, 0, True)})


def test_createdat_ausente_ou_igual_retem_a_fatura():
    for criado in (None, CEDO):
        rows = (*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", criado))
        assert _copias(F4, _lanc_c(*rows)) == ([], {"f4": (2, 0, True)})


def test_createdat_que_nao_le_retem_a_fatura():
    rows = (*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", "ontem"))
    assert _copias(F4, _lanc_c(*rows)) == ([], {"f4": (2, 0, True)})


def test_duas_copias_em_grupos_diferentes_saem_juntas():
    rows = (*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE), ("d", "2026-03-01", 10.0, COMPRA, "f4", TARDE))
    assert _copias(F4, _lanc_c(*rows)) == ([("c", "b"), ("d", "a")], {"f4": (4, 2, False)})


def test_estorno_copiado_sai_e_estorno_de_compra_nao_e_copia():
    # O estorno anula a compra (tipos e sinais diferentes, nunca um grupo); o mesmo estorno sob dois ids deixa a
    # fatura com crédito a mais, e a cópia sai.
    f = _faturas(("f4", "2026-04-17", 120.0))
    rows = (("a", "2026-03-01", 140.0, COMPRA, "f4", CEDO), ("e", "2026-03-05", -20.0, ESTORNO, "f4", CEDO))
    assert _copias(f, _lanc_c(*rows)) == ([], {})
    copia = ("e2", "2026-03-05", -20.0, ESTORNO, "f4", TARDE)
    assert _copias(_faturas(("f4", "2026-04-17", 120.0)), _lanc_c(*rows, copia)) == ([("e2", "e")], {"f4": (2, 1, False)})


def test_tipo_diferente_nao_e_o_mesmo_grupo():
    rows = (*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE, "CREDIT"))
    assert _copias(F4, _lanc_c(*rows)) == ([], {})


def test_pagamento_de_fatura_nunca_e_candidato():
    rows = (*BASE, ("p1", "2026-04-17", -150.0, PAGAMENTO, "f4", CEDO), ("p2", "2026-04-17", -150.0, PAGAMENTO, "f4", TARDE),
            ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE))
    assert _copias(F4, _lanc_c(*rows))[0] == [("c", "b")]


def test_copias_sob_gemeos_reemitidos_sao_da_mesma_fatura():
    bills = _bills(("f4", "2026-04-17"), ("f4-reemitida", "2026-04-17"))
    rows = (*BASE, ("c", "2026-03-21", 140.0, COMPRA, "f4-reemitida", TARDE))
    assert _copias(F4, _lanc_c(*rows), bills) == ([("c", "b")], {"f4": (2, 1, False)})


def test_fatura_aberta_ou_com_historico_incompleto_nao_perde_nada():
    copia = ("c", "2026-03-21", 140.0, COMPRA, "f4", TARDE)
    # Aberta: a fatura dos lançamentos ainda não está em faturas.
    assert _copias(_faturas(("f3", "2026-03-17", 0.0)), _lanc_c(*BASE, copia)) == ([], {})
    # Histórico incompleto: o primeiro lançamento do cartão é depois do início do ciclo.
    tarde = tuple((r[0], "2026-04-01", *r[2:]) for r in (*BASE, copia))
    assert _copias(F4, _lanc_c(*tarde)) == ([], {})


# Propriedade: faturas que fecham, com compras de valores diferentes; copiar algumas compras (com createdAt
# posterior) faz a regra tirar exatamente as cópias, e as faturas voltam a fechar.
@settings(max_examples=60, deadline=None)
@given(valores=st.lists(_centavos, min_size=1, max_size=8, unique=True), data=st.data())
def test_propriedade_tira_exatamente_as_copias(valores, data):
    total = sum(valores) / 100
    faturas = _faturas(("f4", "2026-04-17", round(total, 2)))
    rows = [(f"o{i}", "2026-03-01", v / 100, COMPRA, "f4", CEDO) for i, v in enumerate(valores)]
    copiados = data.draw(st.sets(st.integers(0, len(valores) - 1), min_size=1))
    copias = [(f"c{i}", "2026-03-11", valores[i] / 100, COMPRA, "f4", TARDE) for i in sorted(copiados)]
    removidas, _ = _copias(faturas, _lanc_c(*rows, *copias))
    assert removidas == sorted((f"c{i}", f"o{i}") for i in copiados)
    sobra = _lanc_c(*rows, *copias).filter(~pl.col("id").is_in([c for c, _ in removidas]))
    assert set(_conferir(faturas, sobra.drop("id", "txn_type", "createdAt"))["estado_lancamentos"]) == {FECHA}


# --- a task ---------------------------------------------------------------------------------------------------

def _seed(tmp_path, *extra):
    _delta_util.seed(tmp_path, "curated", "fact_card_statement", FATURAS)
    _delta_util.seed(tmp_path, "curated", "dim_account", pl.DataFrame(
        {"pluggy_account_id": ["p-card-1", "p-bank-1"], "account_uuid": [CARTAO, "banco-conta-0001"]}))
    _delta_util.seed(tmp_path, "cleansed", "bills", pl.DataFrame(
        {"id": ["f4", "f5"], "accountId": ["p-card-1"] * 2,
         "dueDate": ["2026-04-17T00:00:00.000Z", "2026-05-17T00:00:00.000Z"]}))
    # Os lançamentos do cartão, mais um da conta BANK no mesmo billId, que não entra.
    rows = [("p-card-1", "CREDIT", *r) for r in (*LANCAMENTOS, *extra)]
    rows.append(("p-bank-1", "BANK", "2026-04-20", 999.0, COMPRA, "f5"))
    _delta_util.seed(tmp_path, "cleansed", "transactions", pl.DataFrame(
        [(a, t, _d(d), v, c, b) for a, t, d, v, c, b in rows], orient="row",
        schema={"accountId": pl.Utf8, "account_type": pl.Utf8, "date": pl.Date, "amount": pl.Float64,
                "categoryId": pl.Utf8, "cc_bill_id": pl.Utf8}))


def _run(tmp_path):
    return _delta_util.run_script("cleansed2curated", "cleansed2curated_conferencia_fatura",
                                  ["--source", "cleansed", "--target", "curated"], tmp_path,
                                  overrides={"run_day": lambda: HOJE})


def _findings(tmp_path, check):
    return json.loads((tmp_path / "validation" / f"{check.replace(' ', '_')}.json").read_text())["findings"]


def test_task_grava_uma_linha_por_fatura_sem_achado(tmp_path):
    _seed(tmp_path, ("2026-03-20", 140.0, COMPRA, "f4"))
    _run(tmp_path)
    out = _delta_util.read(tmp_path, "curated", "conferencia_fatura").sort("due_date")
    assert out.schema == pl.Schema(COLUNAS)
    assert out.select("account_uuid", "statement_id", "diferenca_lancamentos", "estado_lancamentos",
                      "estado_pagamento").rows() == [
        (CARTAO, "f4", -140.0, NAO_FECHA, FECHA), (CARTAO, "f5", 0.0, FECHA, FECHA)]
    # Só mede: uma fatura que não fecha não é achado.
    assert _findings(tmp_path, "curated.conferencia_fatura") == []
    assert _findings(tmp_path, "curated.conferencia_fatura vs fact_card_statement") == []


def test_task_imprime_contagens_sem_valores(tmp_path, capsys):
    _seed(tmp_path, ("2026-03-20", 140.0, COMPRA, "f4"))
    _run(tmp_path)
    printed = capsys.readouterr().out
    assert ("conferencia_fatura: 2 faturas, lancamentos (tolerância 0.01): fecha 1, nao_fecha 1, "
            "historico_incompleto 0, sem_total 0") in printed
    assert ("conferencia_fatura: 2 faturas, pagamento (tolerância 0.01): fecha 2, nao_fecha 0, "
            "historico_incompleto 0, a_vencer 0, sem_total 0") in printed
    assert not any(v in printed for v in ("140", "150", "80.0", CARTAO, "f4"))
