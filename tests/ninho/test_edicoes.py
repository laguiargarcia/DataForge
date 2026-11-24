"""The app database and the finance edits (df-8x0m.23.13.3, docs/architecture-v2/edits.md): only the
server writes; a correction is saved at once and adds 1 to the version; the pipeline reads the same
database, read-only, and its next "apply" (the reclassificar job) publishes the corrected category."""
import sqlite3

import polars as pl
import pytest

from ninho import banco
from ninho.financas import leitura, regras
from tests.ninho.conftest import AUTH, WORKSPACE, rodar_job



def _classificada(workspace) -> pl.DataFrame:
    return pl.read_delta(str(workspace / "data" / "curated" / leitura.TABELA))


def _alvo(cliente, workspace) -> tuple[str, str]:
    """A transaction and a category it does not have."""
    linha = _classificada(workspace).sort("id").row(0, named=True)
    outras = [c["subcategoria"] for c in cliente.get("/api/financas/edicoes", auth=AUTH).json()["categorias"]
              if c["subcategoria"] != linha["subcategoria"]]
    return linha["id"], sorted(outras)[0]


def test_as_rotas_de_edicao_pedem_a_senha(cliente):
    assert cliente.get("/api/financas/edicoes").status_code == 401
    assert cliente.put("/api/financas/correcoes/x", json={"subcategoria": "y"}).status_code == 401
    assert cliente.delete("/api/financas/correcoes/x").status_code == 401


def test_uma_correcao_e_gravada_soma_1_na_versao_e_o_aplicar_a_publica(cliente, workspace):
    antes = cliente.get("/api/financas/edicoes", auth=AUTH).json()
    assert antes["pendente"] is False
    assert antes["categorias"]
    transacao, nova = _alvo(cliente, workspace)

    r = cliente.put(f"/api/financas/correcoes/{transacao}", json={"subcategoria": nova}, auth=AUTH)
    assert r.status_code == 200, r.text
    depois = cliente.get("/api/financas/edicoes", auth=AUTH).json()
    assert depois["versao"] == antes["versao"] + 1
    assert depois["pendente"] is True
    assert {"transacao_id": transacao, "subcategoria": nova} in depois["correcoes"]
    # saved in the app database, not yet in the published table
    assert _classificada(workspace).filter(pl.col("id") == transacao)["subcategoria"][0] != nova

    rodar_job(workspace.parents[1], "reclassificar")   # the pipeline reads the app database
    linha = _classificada(workspace).filter(pl.col("id") == transacao).row(0, named=True)
    assert (linha["subcategoria"], linha["corrigida"]) == (nova, True)
    assert cliente.get("/api/financas/edicoes", auth=AUTH).json()["pendente"] is False


def test_desfazer_tira_a_correcao_e_soma_1_na_versao(cliente, workspace):
    transacao, nova = _alvo(cliente, workspace)
    cliente.put(f"/api/financas/correcoes/{transacao}", json={"subcategoria": nova}, auth=AUTH)
    versao = cliente.get("/api/financas/edicoes", auth=AUTH).json()["versao"]
    assert cliente.delete(f"/api/financas/correcoes/{transacao}", auth=AUTH).status_code == 204
    depois = cliente.get("/api/financas/edicoes", auth=AUTH).json()
    assert depois["versao"] == versao + 1
    assert transacao not in [c["transacao_id"] for c in depois["correcoes"]]
    assert cliente.delete(f"/api/financas/correcoes/{transacao}", auth=AUTH).status_code == 404


def test_correcoes_recusadas_nao_mudam_a_versao(cliente, workspace):
    transacao, nova = _alvo(cliente, workspace)
    versao = cliente.get("/api/financas/edicoes", auth=AUTH).json()["versao"]
    assert cliente.put("/api/financas/correcoes/nao-existe", json={"subcategoria": nova}, auth=AUTH).status_code == 404
    assert cliente.put(f"/api/financas/correcoes/{transacao}", json={"subcategoria": "categoria que nao existe"},
                       auth=AUTH).status_code == 422
    assert cliente.put(f"/api/financas/correcoes/{transacao}", json={"subcategoria": "  "}, auth=AUTH).status_code == 422
    assert cliente.get("/api/financas/edicoes", auth=AUTH).json()["versao"] == versao


def test_a_abertura_do_pipeline_nao_consegue_escrever(workspace):
    edicao = regras.edicao(workspace)
    conn = edicao.abrir_leitura(banco.caminho(workspace))
    try:
        assert edicao.ler_versao(conn) >= 0
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO correcoes (transacao_id, subcategoria) VALUES ('x', 'y')")
    finally:
        conn.close()


def test_a_abertura_do_pipeline_sem_arquivo_le_vazio_e_nao_cria_nada(workspace, tmp_path):
    edicao = regras.edicao(workspace)
    ausente = tmp_path / "data" / "edicao.db"
    conn = edicao.abrir_leitura(ausente)
    try:
        assert edicao.ler_correcoes(conn).is_empty()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO correcoes (transacao_id, subcategoria) VALUES ('x', 'y')")
    finally:
        conn.close()
    assert not ausente.exists()
    assert not ausente.parent.exists()


@pytest.mark.parametrize("script", ["report/classify.py", "cleansed2curated/cleansed2curated_conferencia_saldo.py"])
def test_os_scripts_do_pipeline_abrem_o_banco_de_edicoes_so_para_leitura(script):
    """Until a gate rule says it (docs/architecture-v2/edits.md): no pipeline script that reads the
    edits goes back to the open that creates and can write."""
    fonte = (WORKSPACE / "scripts" / script).read_text(encoding="utf-8")
    assert "abrir_leitura(" in fonte
    assert " abrir(" not in fonte
    assert "import abrir," not in fonte


def test_pasta_que_nao_e_um_workspace_nao_ganha_banco(cliente, tmp_path, monkeypatch):
    vazia = tmp_path / "vazia"
    vazia.mkdir()
    monkeypatch.setenv("NINHO_WORKSPACE", str(vazia))
    assert cliente.get("/api/financas/edicoes", auth=AUTH).status_code == 503
    assert list(vazia.iterdir()) == []


def test_o_banco_do_app_e_o_arquivo_da_conexao_edicao_do_workspace(workspace):
    assert banco.caminho(workspace) == workspace / "data" / "edicao.db"
