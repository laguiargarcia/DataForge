"""Saúde do mês e detalhamento por categoria (df-cns etapa 1, Task 3) — Resumo!C6:C11 e R6/U6/X6/AA6."""
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

from classificacao import TIPOS_SAIDA
from saude import GRUPO_ENTRADAS, saude_por_categoria, saude_por_mes

_SCHEMA = {"anomes_key": pl.Int32, "lado": pl.String, "Tipo": pl.String, "categoria": pl.String,
           "amount_signed": pl.Float64, "cash_flag": pl.Boolean}
_ROWS = [
    (202606, "entrada", "Salário", "Salário", 1000.0, True),
    (202606, "entrada", "Outras entradas", "Devolução", -50.0, True),
    (202606, "saida", "Essencial", "Alimentação", -300.0, True),
    (202606, "saida", "Essencial", "Alimentação", 20.0, True),
    (202606, "saida", "Não Essencial", "Lazer", -100.0, True),
    (202606, "saida", "Pagamento cartão", "Pagamento cartão", -200.0, True),
    (202606, "neutro", "Investimentos", "Investimentos", -500.0, True),
    (202606, "saida", "Essencial", "Alimentação", -999.0, False),
]


def _classificada(rows=_ROWS):
    return pl.DataFrame(rows, schema=_SCHEMA, orient="row")


def test_grupos():
    assert (GRUPO_ENTRADAS, *TIPOS_SAIDA) == ("Entradas", "Essencial", "Não Essencial", "Pagamento cartão")


def test_saude_por_mes():
    r = saude_por_mes(_classificada()).row(0, named=True)
    assert r["anomes_key"] == 202606
    assert r["mes_ano"] == "jun/2026"
    assert r["entradas"] == 1000.0
    assert r["saidas_essencial"] == 280.0
    assert r["saidas_nao_essencial"] == 100.0
    assert r["saidas_pagamento_cartao"] == 200.0
    assert r["saidas_total"] == 580.0
    assert r["sobra"] == 420.0


def test_saude_por_categoria():
    df = saude_por_categoria(_classificada())
    valor = {(g, c): v for g, c, v in df.select("grupo", "categoria", "valor").rows()}
    assert valor == {("Entradas", "Salário"): 1000.0, ("Essencial", "Alimentação"): 280.0,
                     ("Não Essencial", "Lazer"): 100.0, ("Pagamento cartão", "Pagamento cartão"): 200.0}
    assert set(df["anomes_key"].to_list()) == {202606}


def test_mes_sem_um_tipo_vira_zero():
    r = saude_por_mes(_classificada([(202607, "saida", "Essencial", "Contas", -10.0, True)])).row(0, named=True)
    assert r["entradas"] == 0.0
    assert r["saidas_pagamento_cartao"] == 0.0
    assert r["sobra"] == -10.0


def test_saude_vazia():
    vazio = _classificada([])
    mes = saude_por_mes(vazio)
    cat = saude_por_categoria(vazio)
    assert mes.height == 0
    assert mes.columns == [
        "anomes_key", "mes_ano", "entradas", "saidas_essencial", "saidas_nao_essencial",
        "saidas_pagamento_cartao", "saidas_total", "sobra"]
    assert cat.height == 0
    assert cat.columns == ["anomes_key", "grupo", "categoria", "valor"]


def test_grupo_sem_gasto_e_zero_positivo():
    # −Σ de um filtro vazio dá −0.0, que a tela formataria como "-R$ 0,00".
    # Vários meses: com uma linha só o Polars normalizava sozinho e o teste não pegava o caso.
    rows = [(202605 + i, "entrada", "Salário", "Salário", 100.0, True) for i in range(3)]
    for r in saude_por_mes(_classificada(rows)).iter_rows(named=True):
        for col in ("saidas_essencial", "saidas_nao_essencial", "saidas_pagamento_cartao", "saidas_total"):
            assert r[col] == 0.0, (r["anomes_key"], col)
            assert str(r[col]) == "0.0", (r["anomes_key"], col)
