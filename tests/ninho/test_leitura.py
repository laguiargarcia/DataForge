"""The ninho server reads the published table fact_transaction_classified of the synthetic run
(df-8x0m.23.13.2): `dataforge run GetFinance finance_demo` on a scratch copy writes the lake, and the
route of the month's transactions answers from it, read-only, only to a person logged in who has finances.
"""

import polars as pl
import pytest

from ninho.financas import leitura
from tests.ninho.conftest import ANA, AUTH, Cliente

ROTA = "/api/financas/transacoes"


@pytest.fixture
def cliente(workspace_demo, banco, monkeypatch):
    monkeypatch.setenv("NINHO_WORKSPACE", str(workspace_demo))
    return Cliente()


def _tabela(workspace):
    return pl.read_delta(str(workspace / "data" / "curated" / leitura.TABELA))


def _um_mes(workspace):
    anomes = _tabela(workspace)["anomes_key"].max()
    return f"{anomes // 100}-{anomes % 100:02d}", anomes


def test_transacoes_do_mes_com_a_senha(cliente, workspace_demo):
    mes, anomes = _um_mes(workspace_demo)
    r = cliente.get(ROTA, params={"mes": mes}, auth=AUTH)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["mes"] == mes
    esperadas = _tabela(workspace_demo).filter(pl.col("anomes_key") == anomes)
    assert esperadas.height > 0
    assert len(corpo["transacoes"]) == esperadas.height
    assert {t["id"] for t in corpo["transacoes"]} == set(esperadas["id"].to_list())
    assert all(set(t) == set(leitura.COLUNAS) for t in corpo["transacoes"])
    assert all(t["anomes_key"] == anomes for t in corpo["transacoes"])


def test_transacoes_vem_da_mais_recente_para_a_mais_antiga(cliente, workspace_demo):
    mes, _ = _um_mes(workspace_demo)
    datas = [t["date"] for t in cliente.get(ROTA, params={"mes": mes}, auth=AUTH).json()["transacoes"]]
    assert datas == sorted(datas, reverse=True)


def test_mes_sem_transacao_devolve_lista_vazia(cliente):
    r = cliente.get(ROTA, params={"mes": "1999-01"}, auth=AUTH)
    assert r.status_code == 200
    assert r.json()["transacoes"] == []


@pytest.mark.parametrize("auth", [None, ("ana", "senha-errada"), ("ana", ""), ("", ANA[1])])
def test_sem_a_senha_certa_responde_401(cliente, workspace_demo, auth):
    mes, _ = _um_mes(workspace_demo)
    r = cliente.get(ROTA, params={"mes": mes}, auth=auth)
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers   # nothing that makes a browser show its own box
    assert "transacoes" not in r.json()


def test_sem_ninho_banco_ninguem_entra(cliente, monkeypatch, workspace_demo):
    """The people are in the app database: without it nobody can be checked, finances included."""
    monkeypatch.delenv("NINHO_BANCO")
    mes, _ = _um_mes(workspace_demo)
    assert cliente.get(ROTA, params={"mes": mes}, auth=AUTH).status_code == 503


@pytest.mark.parametrize("mes", ["0000-13", "0000-0", "26-03", "marco", ""])
def test_mes_invalido_responde_422(cliente, mes):
    assert cliente.get(ROTA, params={"mes": mes}, auth=AUTH).status_code == 422


def test_sem_a_tabela_publicada_responde_503(cliente, monkeypatch, tmp_path):
    monkeypatch.setenv("NINHO_WORKSPACE", str(tmp_path))
    assert cliente.get(ROTA, params={"mes": "2026-01"}, auth=AUTH).status_code == 503


def test_a_leitura_nao_escreve_no_lago(cliente, workspace_demo):
    curated = workspace_demo / "data" / "curated" / leitura.TABELA
    antes = sorted((p.relative_to(curated), p.stat().st_mtime_ns) for p in curated.rglob("*"))
    mes, _ = _um_mes(workspace_demo)
    cliente.get(ROTA, params={"mes": mes}, auth=AUTH)
    assert sorted((p.relative_to(curated), p.stat().st_mtime_ns) for p in curated.rglob("*")) == antes
