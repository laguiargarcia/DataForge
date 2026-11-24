"""Scripts das tasks classify / report (df-cns etapa 1, Task 5), de ponta a ponta num tmp_path.

O curated de entrada é uma cópia do curated real do workspace (skip se não existir); o banco de
edição é semeado das fixtures. Nada é gravado no workspace.
"""
import sys
from contextlib import closing
from pathlib import Path

import polars as pl
import pytest

from tests._delta_util import REAL_DATA, SCRIPTS, read, run_script, seed

# Reads a copy of the real curated (read-only, into tmp_path); skips where there is none.
pytestmark = pytest.mark.integration

# Mesmo checkout que o run_script do _delta_util: os
# módulos importados e os scripts executados têm de ser do mesmo lugar.
if not (SCRIPTS / "report" / "classify.py").exists():
    pytest.skip(f"scripts do report ainda não estão em {SCRIPTS.parent.name}", allow_module_level=True)
_CURATED = REAL_DATA / "curated"
_FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(SCRIPTS / "report"))

from classificacao import classificar
from edicao import abrir, carga_inicial
from report_lib import investimentos_per_month
from saude import saude_por_mes

# As tabelas do curated que as tasks classify e report leem e não gravam (test_job_wiring confere com os scripts).
ENTRADAS = ["fact_transaction", "fact_fatura_mes", "fact_balance_snapshot", "fact_investment_position",
            "dim_investment"]
_SAIDAS = ["report_saude", "report_saude_categoria", "report_parcelas", "report_fatura_itens",
           "report_liquidity", "report_cartao", "report_investimentos"]
_ARGV = ["--source", "curated", "--target", "curated"]


def _real(table):
    path = _CURATED / table
    if not (path / "_delta_log").exists():
        pytest.skip(f"curated {table} ausente em {_CURATED}")
    return pl.read_delta(str(path))


def _dic_rules():
    dic = pl.read_csv(_FIXTURES / "dicionario_v3.csv", encoding="utf8", infer_schema_length=0)
    rules = pl.read_csv(_FIXTURES / "mapeamento_v3.csv", encoding="utf8", infer_schema_length=0)
    return dic, rules


def _preparar(tmp_path, so_banco=False, carregar=True):
    tabelas = {t: _real(t) for t in ENTRADAS}
    if so_banco:
        tabelas["fact_transaction"] = tabelas["fact_transaction"].filter(pl.col("account_type") == "BANK")
        tabelas["fact_fatura_mes"] = tabelas["fact_fatura_mes"].clear()
        snap = tabelas["fact_balance_snapshot"]
        tabelas["fact_balance_snapshot"] = snap.filter(pl.col("account_type") == "BANK")
    for t, df in tabelas.items():
        seed(tmp_path, "curated", t, df)
    if carregar:
        with closing(abrir(tmp_path / "edicao")) as conn:
            carga_inicial(conn, *_dic_rules())
    return tabelas


def test_scripts_gravam_tabelas(tmp_path):
    tabelas = _preparar(tmp_path)
    run_script("report", "classify", _ARGV, tmp_path)
    run_script("report", "report_curated", _ARGV, tmp_path)
    classificada = read(tmp_path, "curated", "fact_transaction_classified")
    assert classificada.height == classificar(tabelas["fact_transaction"], *_dic_rules()).height
    for t in _SAIDAS:
        assert (tmp_path / "curated" / t / "_delta_log").exists(), t
    assert read(tmp_path, "curated", "report_saude").equals(saude_por_mes(classificada))
    assert not classificada["corrigida"].any()
    assert read(tmp_path, "curated", "edicao_aplicada")["versao"].to_list() == [0]


def test_scripts_rodam_sem_cartao(tmp_path):
    _preparar(tmp_path, so_banco=True)
    run_script("report", "classify", _ARGV, tmp_path)
    run_script("report", "report_curated", _ARGV, tmp_path)
    assert read(tmp_path, "curated", "report_parcelas").height == 0
    assert read(tmp_path, "curated", "report_fatura_itens").height == 0
    assert read(tmp_path, "curated", "report_saude").height > 0


def test_classify_aplica_correcao_e_grava_versao(tmp_path):
    tabelas = _preparar(tmp_path)
    # Uma saída de caixa que não é investimento: a correção para Investimentos tem de mudar o aplicado.
    alvo = tabelas["fact_transaction"].filter(
        ~pl.col("netting_applied").fill_null(False) & pl.col("cash_flag").fill_null(False)
        & pl.col("etl_tag").ne_missing("investment") & (pl.col("amount_signed") < 0)
    )["id"][0]
    conn = abrir(tmp_path / "edicao")
    with conn:
        conn.execute("INSERT INTO correcoes VALUES (?, 'Investimentos'), ('id-que-nao-existe', 'Investimentos')",
                     (alvo,))
        conn.execute("INSERT INTO versao (id, numero) VALUES (1, 5)")
    conn.close()
    run_script("report", "classify", _ARGV, tmp_path)
    classificada = read(tmp_path, "curated", "fact_transaction_classified")
    assert classificada.filter(pl.col("corrigida"))["id"].to_list() == [alvo]
    assert classificada.filter(pl.col("id") == alvo)["subcategoria"].item() == "Investimentos"
    # A correção para Investimentos chega também ao fluxo de investimento (df-cns.8).
    run_script("report", "report_curated", _ARGV, tmp_path)
    esperado = investimentos_per_month(tabelas["fact_transaction"], tabelas["fact_investment_position"],
                                       pl.DataFrame({"id": [alvo], "subcategoria": ["Investimentos"]}),
                                       tabelas["dim_investment"])
    assert read(tmp_path, "curated", "report_investimentos").equals(esperado)
    assert not esperado.equals(investimentos_per_month(tabelas["fact_transaction"], tabelas["fact_investment_position"],
                                                       holdings=tabelas["dim_investment"]))
    aplicada = read(tmp_path, "curated", "edicao_aplicada")
    assert aplicada["versao"].to_list() == [5]
    assert aplicada.schema["aplicada_em"] == pl.Datetime("us", "UTC")


def test_classify_sem_banco_falha(tmp_path):
    _preparar(tmp_path, carregar=False)
    with pytest.raises(RuntimeError, match="carga_inicial_edicao"):
        run_script("report", "classify", _ARGV, tmp_path)


def test_validacao_b10_patrimonio(tmp_path):
    _preparar(tmp_path)
    run_script("report", "classify", _ARGV, tmp_path)
    run_script("report", "report_curated", _ARGV, tmp_path)
    liq = read(tmp_path, "curated", "report_liquidity").drop_nulls(
        ["patrimonio", "caixa_saldo", "investimentos_posicao", "card_outstanding"])
    assert liq.height > 0
    delta = liq["patrimonio"] - (liq["caixa_saldo"] + liq["investimentos_posicao"] - liq["card_outstanding"])
    assert (delta.abs() <= 0.01).all()
