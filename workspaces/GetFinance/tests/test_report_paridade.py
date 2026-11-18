"""Checagens do _validacao do Resumo v3 (df-cns etapa 1) sobre o curated real do workspace.

A paridade linha a linha e mês a mês com o oráculo antigo foi provada na migração (2026-11-09);
desde então o oráculo importa este código, então ficam só as invariantes B2–B12.
"""
import sys
from pathlib import Path

import polars as pl
import pytest

from tests._delta_util import REAL_DATA, SCRIPTS

_CURATED = REAL_DATA / "curated"
pytestmark = pytest.mark.integration   # the real curated, read-only; skips where there is none
_FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(SCRIPTS / "report"))

from classificacao import classificar
from cartao_listas import itens_proxima_fatura, parcelas_ativas, ultimo_mes
from report_lib import cartao_per_month
from classificacao import TIPOS_SAIDA
from saude import GRUPO_ENTRADAS, saude_por_categoria, saude_por_mes

_TOL = 0.01


def _curated(table: str) -> pl.DataFrame:
    path = _CURATED / table
    if not (path / "_delta_log").exists():
        pytest.skip(f"curated {table} ausente em {_CURATED}")
    return pl.read_delta(str(path))


@pytest.fixture(scope="module")
def curated_fact() -> pl.DataFrame:
    return _curated("fact_transaction")


@pytest.fixture(scope="module")
def dic() -> pl.DataFrame:
    return pl.read_csv(_FIXTURES / "dicionario_v3.csv", encoding="utf8", infer_schema_length=0)


@pytest.fixture(scope="module")
def rules() -> pl.DataFrame:
    return pl.read_csv(_FIXTURES / "mapeamento_v3.csv", encoding="utf8", infer_schema_length=0)


@pytest.fixture(scope="module")
def classificada(curated_fact, dic, rules) -> pl.DataFrame:
    return classificar(curated_fact, dic, rules)




# ----------------------------- Saúde (Task 3) ----------------------------- #
_COLS_SAUDE = ["entradas", "saidas_essencial", "saidas_nao_essencial", "saidas_pagamento_cartao",
               "saidas_total", "sobra"]


@pytest.fixture(scope="module")
def saude(classificada) -> pl.DataFrame:
    return saude_por_mes(classificada)


def test_validacao_b2_soma_das_saidas(saude):
    soma = saude["saidas_essencial"] + saude["saidas_nao_essencial"] + saude["saidas_pagamento_cartao"]
    assert ((soma - saude["saidas_total"]).abs() <= _TOL).all()


def test_validacao_b3_sobra(saude):
    assert ((saude["entradas"] - saude["saidas_total"] - saude["sobra"]).abs() <= _TOL).all()


def test_validacao_b4_b5_nenhuma_entrada_negativa(classificada):
    neg = classificada.filter(pl.col("cash_flag") & (pl.col("lado") == "entrada") & (pl.col("amount_signed") < 0))
    assert neg.height == 0, neg.select("anomes_key", "id")


def test_validacao_b6_b9_detalhamento_soma_o_total(classificada, saude):
    col = {"Entradas": "entradas", "Essencial": "saidas_essencial",
           "Não Essencial": "saidas_nao_essencial", "Pagamento cartão": "saidas_pagamento_cartao"}
    assert set(col) == {GRUPO_ENTRADAS, *TIPOS_SAIDA}
    por_grupo = saude_por_categoria(classificada).group_by("anomes_key", "grupo").agg(pl.col("valor").sum())
    for r in saude.iter_rows(named=True):
        for grupo, c in col.items():
            got = por_grupo.filter((pl.col("anomes_key") == r["anomes_key"]) & (pl.col("grupo") == grupo))["valor"].sum()
            assert abs(got - r[c]) <= _TOL, (r["anomes_key"], grupo, got, r[c])


# ----------------------------- Cartão (Task 4) ----------------------------- #
@pytest.fixture(scope="module")
def fatura() -> pl.DataFrame:
    return _curated("fact_fatura_mes")


@pytest.fixture(scope="module")
def cartao(curated_fact, fatura) -> pl.DataFrame:
    return cartao_per_month(_curated("fact_balance_snapshot"), fatura, curated_fact)


def _no_ultimo_mes(cartao, col):
    m = ultimo_mes(cartao)
    if m is None:
        pytest.skip("sem cartão no curated")
    return m, cartao.filter(pl.col("anomes_key") == m)[col].item()


@pytest.mark.xfail(strict=True, reason="df-nvio: card balance differs from the pending bills in the real data")
def test_validacao_b11_itens_somam_proxima_fatura(classificada, fatura, cartao):
    m, esperado = _no_ultimo_mes(cartao, "proxima_fatura")
    itens = itens_proxima_fatura(classificada, fatura, m)
    assert abs(itens["valor"].sum() - (esperado or 0.0)) <= _TOL


def test_validacao_b12_parcelas_somam_comprometido(curated_fact, classificada, fatura, cartao):
    m, esperado = _no_ultimo_mes(cartao, "comprometido_parcelas")
    parcelas = parcelas_ativas(curated_fact, classificada, fatura, m)
    assert abs(parcelas["valor_restante"].sum() - (esperado or 0.0)) <= _TOL
