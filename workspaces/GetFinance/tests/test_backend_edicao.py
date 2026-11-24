"""Backend do app Finanças (df-cns.8.2): as rotas de edição sobre um workspace de mentira em tmp_path.

O backend é importado do arquivo (apps/financas/backend/main.py), como o uvicorn faz, e chamado com
o segredo que o DataForge mandaria. A API do DataForge é um httpx.MockTransport.
"""
import importlib.util
import shutil
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from dataforge.app_guard import TOKEN_ENV, TOKEN_HEADER

WS = Path(__file__).resolve().parent.parent
TOKEN = "segredo-de-teste"

_spec = importlib.util.spec_from_file_location("financas_backend", WS / "apps" / "financas" / "backend" / "main.py")
backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backend)
import edicao  # o backend já pôs scripts/report no sys.path

DIC = pl.DataFrame({"subcategoria": ["Groceries", "Contas", "Salario"],
                    "categoria": ["Alimentação", "Contas", "Salário"],
                    "Tipo": ["Essencial", "Essencial", "Salário"]})
REGRAS = pl.DataFrame({"padrao": ["MERCADO"], "subcategoria": ["Groceries"]})


def _fact():
    linhas = [("t1", "BANK", "DEBIT", "MERCADO CENTRAL", None, False),
              ("t2", "BANK", "CREDIT", "MERCADO REEMBOLSO", None, False),
              ("t3", "BANK", "DEBIT", "APLICACAO", "investment", False),
              ("t4", "BANK", "DEBIT", "MERCADO ANULADO", None, True)]
    return pl.DataFrame(
        [{"id": i, "date": date(2026, 6, 15), "account_type": a, "txn_type": t, "description_normalized": d,
          "category": None, "income_source_key": None, "etl_tag": tag, "netting_applied": n}
         for i, a, t, d, tag, n in linhas],
        schema={"id": pl.String, "date": pl.Date, "account_type": pl.String, "txn_type": pl.String,
                "description_normalized": pl.String, "category": pl.String, "income_source_key": pl.String,
                "etl_tag": pl.String, "netting_applied": pl.Boolean},
    )


def _conferencia():
    # Dois intervalos da conta: um que não fecha (+9,93) e um que fecha.
    utc = pl.Datetime("us", "UTC")
    return pl.DataFrame({
        "account_uuid": ["conta-ficticia", "conta-ficticia"],
        "ate": ["2026-05-28T19:23:28.936Z", "2026-05-29T19:00:00Z"],
        "diferenca": [9.93, 0.0], "estado": ["nao_fecha", "fecha"],
    }).with_columns(pl.col("ate").str.to_datetime(time_unit="us", time_zone="UTC").cast(utc))


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Workspace em tmp_path: connections.yaml do de verdade, curated com 4 transações (t4 anulada),
    banco de edição semeado e edicao_aplicada na versão 0."""
    (tmp_path / "connections").mkdir()
    shutil.copy(WS / "connections" / "connections.yaml", tmp_path / "connections" / "connections.yaml")
    curated = tmp_path / "data" / "curated"
    fact = _fact()
    fact.write_delta(str(curated / "fact_transaction"))
    fact.filter(~pl.col("netting_applied")).select("id").write_delta(str(curated / "fact_transaction_classified"))
    pl.DataFrame({"versao": [0]}).write_delta(str(curated / "edicao_aplicada"))
    _conferencia().write_delta(str(curated / "conferencia_saldo"))
    conn = edicao.abrir(tmp_path / "data" / "edicao.db")
    edicao.carga_inicial(conn, DIC, REGRAS)
    conn.close()
    monkeypatch.setenv("DATAFORGE_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("DATAFORGE_WORKSPACE", "Meu WS")
    monkeypatch.setenv("DATAFORGE_API", "http://127.0.0.1:1")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return tmp_path


@pytest.fixture
def cliente(ws):
    with TestClient(backend.app, headers={TOKEN_HEADER: TOKEN}) as c:
        yield c


def _versao(ws):
    with closing(sqlite3.connect(ws / "data" / "edicao.db")) as conn:
        return conn.execute("SELECT numero FROM versao").fetchone()


def _categoria(sub="Lazer", categoria="Lazer", tipo="Não Essencial"):
    return {"subcategoria": sub, "categoria": categoria, "tipo": tipo}


# --- leitura e segredo ------------------------------------------------------------------------------

def test_sem_segredo_recusa(ws):
    with TestClient(backend.app) as c:
        assert c.get("/edicao").status_code == 403


def test_ler_edicao(cliente):
    corpo = cliente.get("/edicao").json()
    assert [c["subcategoria"] for c in corpo["categorias"]] == ["Groceries", "Contas", "Salario"]
    assert corpo["categorias"][0] == {"subcategoria": "Groceries", "categoria": "Alimentação", "tipo": "Essencial"}
    assert corpo["regras"] == [{"padrao": "MERCADO", "subcategoria": "Groceries"}]
    assert corpo["correcoes"] == []
    assert corpo["explicacoes"] == []
    assert corpo["tipos"] == list(edicao.TIPOS)
    assert (corpo["versao"], corpo["aplicada"], corpo["pendente"]) == (0, 0, False)


def test_cada_gravacao_sobe_a_versao_e_deixa_pendente(cliente, ws):
    cliente.post("/categorias", json=_categoria())
    cliente.post("/regras", json={"padrao": "CINEMA", "subcategoria": "Lazer"})
    cliente.put("/correcoes/t1", json={"subcategoria": "Contas"})
    corpo = cliente.get("/edicao").json()
    assert (corpo["versao"], corpo["aplicada"], corpo["pendente"]) == (3, 0, True)
    assert _versao(ws) == (3,)


def test_recusa_nao_sobe_a_versao(cliente):
    assert cliente.post("/categorias", json=_categoria(tipo="Inventado")).status_code == 422
    assert cliente.get("/edicao").json()["versao"] == 0


def test_sem_edicao_aplicada_nada_pendente(cliente, ws):
    shutil.rmtree(ws / "data" / "curated" / "edicao_aplicada")
    corpo = cliente.get("/edicao").json()
    assert (corpo["aplicada"], corpo["pendente"]) == (None, False)


# --- categorias -------------------------------------------------------------------------------------

def test_criar_categoria(cliente):
    assert cliente.post("/categorias", json=_categoria()).status_code == 201
    assert _categoria() in cliente.get("/edicao").json()["categorias"]


@pytest.mark.parametrize(("corpo", "trecho"), [
    (_categoria(tipo="Lazer"), "fora da lista"),
    (_categoria(sub="  "), "subcategoria não pode ficar vazio"),
    (_categoria(categoria=""), "categoria não pode ficar vazio"),
])
def test_categoria_invalida(cliente, corpo, trecho):
    resposta = cliente.post("/categorias", json=corpo)
    assert resposta.status_code == 422
    assert trecho in resposta.json()["detail"]


def test_subcategoria_duplicada(cliente):
    resposta = cliente.post("/categorias", json=_categoria(sub="Groceries"))
    assert resposta.status_code == 409
    assert "já existe" in resposta.json()["detail"]


def test_renomear_leva_regras_e_correcoes(cliente):
    cliente.put("/correcoes/t1", json={"subcategoria": "Groceries"})
    resposta = cliente.put("/categorias/Groceries", json=_categoria("Mercado", "Alimentação", "Essencial"))
    assert resposta.status_code == 200
    corpo = cliente.get("/edicao").json()
    assert "Groceries" not in [c["subcategoria"] for c in corpo["categorias"]]
    assert corpo["regras"] == [{"padrao": "MERCADO", "subcategoria": "Mercado"}]
    assert corpo["correcoes"][0]["subcategoria"] == "Mercado"


def test_renomear_para_nome_existente(cliente):
    assert cliente.put("/categorias/Groceries", json=_categoria("Contas", "x", "Essencial")).status_code == 409


def test_alterar_categoria_ausente(cliente):
    assert cliente.put("/categorias/Nada", json=_categoria()).status_code == 404


def test_subcategoria_com_barra(cliente):
    cliente.post("/categorias", json=_categoria(sub="Casa/Reforma"))
    assert cliente.put("/categorias/Casa%2FReforma", json=_categoria(sub="Casa/Reforma", categoria="Casa")).status_code == 200
    assert cliente.delete("/categorias/Casa%2FReforma").status_code == 204


def test_excluir_em_uso(cliente):
    cliente.put("/correcoes/t1", json={"subcategoria": "Groceries"})
    resposta = cliente.delete("/categorias/Groceries")
    assert resposta.status_code == 409
    assert resposta.json()["detail"].endswith("em uso: 1 regra(s) e 1 correção(ões)")


def test_excluir_livre_e_ausente(cliente):
    assert cliente.delete("/categorias/Contas").status_code == 204
    assert cliente.delete("/categorias/Contas").status_code == 404


# --- regras -----------------------------------------------------------------------------------------

def test_criar_regra_traz_o_efeito(cliente):
    resposta = cliente.post("/regras", json={"padrao": "CENTRAL", "subcategoria": "Contas"})
    assert resposta.status_code == 201
    # t1 é MERCADO CENTRAL: CENTRAL e MERCADO têm 7 letras, empate por subcategoria (Contas < Groceries).
    assert resposta.json()["efeito"] == {"casa": 1, "vence": 1, "barradas": 0}


def test_efeito_mostra_as_barradas_pela_direcao(cliente):
    resposta = cliente.put("/regras/MERCADO", json={"padrao": "MERCADO", "subcategoria": "Groceries"})
    # t1 (saída) e t2 (entrada: barrada, o alvo é de saída); t4 anulada não conta.
    assert resposta.json()["efeito"] == {"casa": 2, "vence": 2, "barradas": 1}


def test_efeito_sem_curated(cliente, ws):
    shutil.rmtree(ws / "data" / "curated" / "fact_transaction")
    assert cliente.post("/regras", json={"padrao": "X", "subcategoria": "Contas"}).json()["efeito"] is None


@pytest.mark.parametrize(("padrao", "status", "trecho"), [
    ("", 422, "não pode ficar vazio"),
    (" MERC", 422, "espaço"),
    ("mercado", 409, "já existe a regra 'MERCADO'"),
])
def test_regra_invalida(cliente, padrao, status, trecho):
    resposta = cliente.post("/regras", json={"padrao": padrao, "subcategoria": "Contas"})
    assert resposta.status_code == status
    assert trecho in resposta.json()["detail"]


def test_regra_com_alvo_fora_do_dicionario(cliente):
    resposta = cliente.post("/regras", json={"padrao": "UBER", "subcategoria": "Transporte"})
    assert resposta.status_code == 422
    assert "não está no dicionário" in resposta.json()["detail"]


def test_alterar_regra_pode_mudar_so_a_caixa_do_padrao(cliente):
    assert cliente.put("/regras/MERCADO", json={"padrao": "Mercado", "subcategoria": "Groceries"}).status_code == 200
    assert cliente.get("/edicao").json()["regras"] == [{"padrao": "Mercado", "subcategoria": "Groceries"}]


def test_excluir_regra(cliente):
    assert cliente.delete("/regras/MERCADO").status_code == 204
    assert cliente.delete("/regras/MERCADO").status_code == 404


# --- correções --------------------------------------------------------------------------------------

def test_corrigir_e_trocar(cliente):
    assert cliente.put("/correcoes/t3", json={"subcategoria": "Contas"}).status_code == 200
    assert cliente.put("/correcoes/t3", json={"subcategoria": "Groceries"}).status_code == 200
    assert cliente.get("/edicao").json()["correcoes"] == [
        {"transacao_id": "t3", "subcategoria": "Groceries", "encontrada": True}]


@pytest.mark.parametrize("transacao_id", ["nao-existe", "t4"])
def test_corrigir_transacao_que_a_classificacao_nao_tem(cliente, transacao_id):
    resposta = cliente.put(f"/correcoes/{transacao_id}", json={"subcategoria": "Contas"})
    assert resposta.status_code == 422
    assert "não encontrada" in resposta.json()["detail"]


def test_corrigir_para_subcategoria_fora_do_dicionario(cliente):
    assert cliente.put("/correcoes/t1", json={"subcategoria": "Nada"}).status_code == 422


def test_correcao_orfa_aparece_como_nao_encontrada(cliente, ws):
    cliente.put("/correcoes/t1", json={"subcategoria": "Contas"})
    pl.DataFrame({"id": ["t2"]}).write_delta(str(ws / "data" / "curated" / "fact_transaction_classified"),
                                            mode="overwrite")
    assert cliente.get("/edicao").json()["correcoes"] == [
        {"transacao_id": "t1", "subcategoria": "Contas", "encontrada": False}]
    assert cliente.delete("/correcoes/t1").status_code == 204
    assert cliente.delete("/correcoes/t1").status_code == 404


# --- aplicar ----------------------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    """A API do DataForge: guarda os pedidos e responde como as rotas de verdade."""
    pedidos = []
    execucoes = [{"run_id": "r-velha", "job": "finance_daily", "status": "success", "reason": None},
                 {"run_id": "r1", "job": "reclassificar", "status": "pending", "reason": None}]

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if pedido.method == "POST":
            return httpx.Response(202, json={"execution_id": "r1"})
        return httpx.Response(200, json={"items": execucoes, "total": len(execucoes)})

    monkeypatch.setattr(backend, "_api", lambda: httpx.Client(base_url="http://df", transport=httpx.MockTransport(responder)))
    return pedidos, execucoes


def test_aplicar_dispara_o_reclassificar(cliente, api):
    pedidos, _ = api
    resposta = cliente.post("/aplicar")
    assert resposta.status_code == 202
    assert resposta.json() == {"execution_id": "r1"}
    assert pedidos[0].method == "POST"
    assert pedidos[0].url.raw_path == b"/api/workspaces/Meu%20WS/jobs/reclassificar/run"


def test_andamento_da_execucao(cliente, api):
    _, execucoes = api
    assert cliente.get("/aplicar/r1").json() == {"status": "pending", "reason": None}
    execucoes[1].update(status="failed", reason="classify falhou")
    assert cliente.get("/aplicar/r1").json() == {"status": "failed", "reason": "classify falhou"}
    assert cliente.get("/aplicar/outra").status_code == 404


def test_aplicar_com_dataforge_fora_do_ar(cliente, monkeypatch):
    def cair(pedido):
        raise httpx.ConnectError("recusado", request=pedido)
    monkeypatch.setattr(backend, "_api", lambda: httpx.Client(base_url="http://df", transport=httpx.MockTransport(cair)))
    resposta = cliente.post("/aplicar")
    assert resposta.status_code == 502
    assert "DataForge não respondeu" in resposta.json()["detail"]


def test_aplicar_com_erro_do_dataforge(cliente, monkeypatch):
    monkeypatch.setattr(backend, "_api", lambda: httpx.Client(
        base_url="http://df", transport=httpx.MockTransport(lambda _: httpx.Response(404, json={}))))
    assert cliente.post("/aplicar").status_code == 502


# --- divergência explicada da conferência pelo saldo (df-y26v) -----------------------------------

ATE = "2026-05-28T19:23:28.936000+00:00"
ROTA = "/explicacoes/conta-ficticia/"


def test_explicar_grava_a_diferenca_do_curated_e_desfazer_apaga(cliente, ws):
    # O app manda o ate como a API de tabelas o serve; o banco guarda o instante normalizado.
    r = cliente.put(ROTA + "2026-05-28T19:23:28.936Z", json={"motivo": " seguro debitado antes da coleta "})
    assert r.status_code == 200
    assert r.json() == {"conta": "conta-ficticia", "ate": ATE, "diferenca": 9.93,
                        "motivo": "seguro debitado antes da coleta"}
    corpo = cliente.get("/edicao").json()
    assert corpo["explicacoes"] == [{"conta": "conta-ficticia", "ate": ATE, "diferenca": 9.93,
                                     "motivo": "seguro debitado antes da coleta"}]
    assert corpo["pendente"] is True
    assert cliente.delete(ROTA + ATE).status_code == 204
    assert cliente.get("/edicao").json()["explicacoes"] == []
    assert cliente.delete(ROTA + ATE).status_code == 404


@pytest.mark.parametrize("caminho,trecho", [
    (ROTA + "2026-05-29T19:00:00Z", "já fecha"),                     # intervalo que fecha
    (ROTA + "2026-05-27T19:00:00Z", "não encontrado"),               # intervalo que não existe
    ("/explicacoes/outra-conta/2026-05-28T19:23:28.936Z", "não encontrado"),
    (ROTA + "ontem", "não é um instante"),
])
def test_explicar_recusa(cliente, caminho, trecho):
    r = cliente.put(caminho, json={"motivo": "um motivo"})
    assert r.status_code == 422
    assert trecho in r.json()["detail"]


def test_explicar_com_o_ate_codificado_como_o_app_manda(cliente):
    # O app codifica ':', '.' e '+' do instante (seg() em edicao.ts).
    r = cliente.put(ROTA + "2026-05-28T19%3A23%3A28%2E936000%2B00%3A00", json={"motivo": "um motivo"})
    assert (r.status_code, r.json()["ate"]) == (200, ATE)
    assert cliente.delete(ROTA + "2026-05-28T19%3A23%3A28%2E936000%2B00%3A00").status_code == 204


def test_explicar_sem_motivo(cliente):
    r = cliente.put(ROTA + ATE, json={"motivo": "  "})
    assert r.status_code == 422
    assert "motivo" in r.json()["detail"]
