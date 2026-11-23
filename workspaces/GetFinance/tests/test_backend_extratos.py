"""Backend do app Finanças (df-7xk9.2): POST /extratos confere o PDF, guarda em data/extratos e pede o job
extrato; GET /extratos lista os guardados.

Como tests/test_backend_edicao.py: o backend importado do arquivo, um workspace de mentira em tmp_path e a API
do DataForge num httpx.MockTransport. Os PDFs são feitos aqui com o texto falso de tests/test_extrato.py.
"""
import importlib.util
import shutil
from datetime import date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from dataforge.app_guard import TOKEN_ENV, TOKEN_HEADER
from tests import _delta_util
from tests.test_extrato import A, B, _cabecalho, _lista, _pdf

WS = Path(__file__).resolve().parent.parent
TOKEN = "segredo-de-teste"
BANK, OUTRO = "p-bank-1", "p-bank-2"
CONTA, CONTA_OUTRO = "banco-checking-3456", "banco-checking-9999"
NOME_A = "extrato_2026-05-31_2026-06-05.pdf"

_spec = importlib.util.spec_from_file_location("financas_backend_extratos",
                                               WS / "apps" / "financas" / "backend" / "main.py")
backend = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backend)

# O extrato de outra conta da mesma agência, no mesmo período de A.
A_OUTRO = [A[0].replace(_cabecalho(), _cabecalho(conta="99999-9"))]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Workspace em tmp_path: connections.yaml do de verdade, duas contas BANK e um cartão em
    cleansed.accounts, e dim_account; sem data/extratos."""
    (tmp_path / "connections").mkdir()
    shutil.copy(WS / "connections" / "connections.yaml", tmp_path / "connections" / "connections.yaml")
    data = tmp_path / "data"
    _delta_util.seed(data, "cleansed", "accounts", pl.DataFrame(
        {"id": [BANK, OUTRO, "p-card-1"], "type": ["BANK", "BANK", "CREDIT"],
         "number": ["0001234567-8", "99999-9", "1234"],
         "transfer_number": ["901/0001/0001234567-8", "901/0001/99999-9", None]}))
    _delta_util.seed(data, "curated", "dim_account", pl.DataFrame(
        {"pluggy_account_id": [BANK, OUTRO, "p-card-1"], "account_uuid": [CONTA, CONTA_OUTRO, "banco-credit-1234"]}))
    monkeypatch.setenv("DATAFORGE_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("DATAFORGE_WORKSPACE", "Meu WS")
    monkeypatch.setenv("DATAFORGE_API", "http://127.0.0.1:1")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    return tmp_path


@pytest.fixture
def api(monkeypatch):
    """A API do DataForge: guarda os pedidos e aceita o job como a rota de verdade."""
    pedidos = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        return httpx.Response(202, json={"execution_id": "r-extrato"})

    monkeypatch.setattr(backend, "_api", lambda: httpx.Client(base_url="http://df",
                                                              transport=httpx.MockTransport(responder)))
    return pedidos


@pytest.fixture
def cliente(ws, api):
    with TestClient(backend.app, headers={TOKEN_HEADER: TOKEN}) as c:
        yield c


def _pasta(ws):
    return ws / "data" / "extratos"


def _guardados(ws):
    pasta = _pasta(ws)
    return sorted(f.name for f in pasta.iterdir()) if pasta.exists() else []


def _subir(cliente, corpo, **headers):
    return cliente.post("/extratos", content=corpo, headers={"Content-Type": "application/pdf", **headers})


# --- POST /extratos ---------------------------------------------------------------------------------

def test_pdf_aceito_e_guardado_pelo_periodo_e_pede_o_job_extrato(cliente, ws, api):
    corpo = _pdf(A)
    resposta = _subir(cliente, corpo)
    assert resposta.status_code == 201, resposta.text
    assert resposta.json() == {"arquivo": NOME_A, "periodo": {"de": "2026-05-31", "ate": "2026-06-05"},
                               "n_lancamentos": 4, "n_saldos": 3, "linhas_ignoradas": 0,
                               "dias_divergentes": [], "execution_id": "r-extrato"}
    assert _guardados(ws) == [NOME_A]
    assert (_pasta(ws) / NOME_A).read_bytes() == corpo
    assert [(p.method, p.url.raw_path) for p in api] == [("POST", b"/api/workspaces/Meu%20WS/jobs/extrato/run")]


def test_o_nome_guardado_nao_traz_o_nome_enviado_nem_a_conta(cliente, ws):
    resposta = _subir(cliente, _pdf(A), **{"Content-Disposition": 'attachment; filename="Extrato 1234567-8.pdf"'})
    assert resposta.status_code == 201
    nome = resposta.json()["arquivo"]
    assert _guardados(ws) == [nome]
    assert "Extrato " not in nome
    assert not any(trecho in nome for trecho in ("1234567", "0001", "99999"))


@pytest.mark.parametrize("corpo", [b"", b"isto nao e um pdf", _pdf(["um PDF qualquer\nsem extrato"]),
                                   _pdf([_cabecalho() + "\n03/06/2026 COMPRA SEM FIM -1,00"]), _pdf(_lista())],
                         ids=["vazio", "texto", "outro-pdf", "cortado", "sem-dias"])
def test_corpo_que_nao_e_extrato_e_recusado_sem_gravar(cliente, ws, api, corpo):
    resposta = _subir(cliente, corpo)
    assert resposta.status_code == 422
    assert "extrato" in resposta.json()["detail"] or "PDF" in resposta.json()["detail"]
    assert _guardados(ws) == []
    assert api == []


@pytest.mark.parametrize("conta", ["7654321-0", "99999-9"], ids=["nenhuma", "duas"])
def test_conta_que_nao_casa_com_exatamente_uma_bank_e_recusada_sem_gravar(cliente, ws, api, conta):
    if conta == "99999-9":   # duas contas BANK com o mesmo número: o extrato não diz qual
        _delta_util.seed(ws / "data", "cleansed", "accounts", pl.DataFrame(
            {"id": [BANK, OUTRO], "type": ["BANK", "BANK"], "number": ["99999-9", "99999-9"],
             "transfer_number": ["901/0001/99999-9", "901/0001/99999-9"]}))
    resposta = _subir(cliente, _pdf([A[0].replace(_cabecalho(), _cabecalho(conta=conta))]))
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert "conta" in detalhe
    assert conta not in detalhe
    assert conta.replace("-", "") not in detalhe
    assert _guardados(ws) == []
    assert api == []


def test_sem_cleansed_accounts_nenhuma_conta_casa(cliente, ws):
    shutil.rmtree(ws / "data" / "cleansed" / "accounts")
    assert _subir(cliente, _pdf(A)).status_code == 422
    assert _guardados(ws) == []


def test_mesmo_periodo_e_conta_ja_guardado_e_recusado_sem_sobrescrever(cliente, ws, api):
    _pasta(ws).mkdir(parents=True)
    antes = _pdf(A)
    (_pasta(ws) / NOME_A).write_bytes(antes)
    resposta = _subir(cliente, _pdf([A[0].replace("05/06/2026 10:00:00", "06/06/2026 08:00:00")]))
    assert resposta.status_code == 409
    assert "já" in resposta.json()["detail"]
    assert _guardados(ws) == [NOME_A]
    assert (_pasta(ws) / NOME_A).read_bytes() == antes
    assert api == []


def test_outra_conta_no_mesmo_periodo_ganha_sufixo(cliente, ws):
    _pasta(ws).mkdir(parents=True)
    (_pasta(ws) / NOME_A).write_bytes(_pdf(A_OUTRO))
    (_pasta(ws) / "extrato_2026-05-31_2026-06-05-2.pdf").write_bytes(b"guardado que nao se le")
    resposta = _subir(cliente, _pdf(A))
    assert resposta.status_code == 201
    assert resposta.json()["arquivo"] == "extrato_2026-05-31_2026-06-05-3.pdf"
    assert len(_guardados(ws)) == 3


def test_corpo_acima_do_limite_e_recusado(cliente, ws, api, monkeypatch):
    monkeypatch.setattr(backend, "LIMITE_EXTRATO", 1000)
    resposta = _subir(cliente, b"%PDF-" + b"x" * 1000)
    assert resposta.status_code == 413
    assert "MB" in resposta.json()["detail"]
    assert _guardados(ws) == []
    assert api == []


def test_corpo_acima_do_limite_sem_content_length_e_recusado(cliente, ws, monkeypatch):
    monkeypatch.setattr(backend, "LIMITE_EXTRATO", 1000)
    resposta = cliente.post("/extratos", content=iter([b"x" * 600, b"x" * 600]))
    assert resposta.status_code == 413
    assert _guardados(ws) == []


def test_saldo_diferente_do_guardado_e_aceito_e_o_dia_volta_em_dias_divergentes(cliente, ws):
    _delta_util.seed(ws / "data", "curated", "extrato_saldos", pl.DataFrame({
        "account_uuid": [CONTA, CONTA, CONTA_OUTRO],
        "dia": [date(2026, 6, 2), date(2026, 6, 4), date(2026, 5, 31)],
        "arquivo": ["velho.pdf"] * 3, "saldo_do_dia": [999.0, 1240.0, 1.0]}))
    resposta = _subir(cliente, _pdf(A))
    assert resposta.status_code == 201
    assert resposta.json()["dias_divergentes"] == ["2026-06-02"]
    assert _guardados(ws) == [NOME_A]


def test_dataforge_fora_do_ar_deixa_o_pdf_guardado_e_diz_isso(cliente, ws, monkeypatch):
    def cair(pedido):
        raise httpx.ConnectError("recusado", request=pedido)
    monkeypatch.setattr(backend, "_api", lambda: httpx.Client(base_url="http://df", transport=httpx.MockTransport(cair)))
    resposta = _subir(cliente, _pdf(A))
    assert resposta.status_code == 502
    assert f"guardado como {NOME_A}" in resposta.json()["detail"]
    assert "DataForge não respondeu" in resposta.json()["detail"]
    assert _guardados(ws) == [NOME_A]


# --- GET /extratos ----------------------------------------------------------------------------------

def test_listar_sem_tabela_e_lista_vazia(cliente):
    assert cliente.get("/extratos").json() == {"arquivos": []}


def test_listar_traz_as_linhas_de_extrato_arquivos(cliente, ws):
    emitido = datetime(2026, 6, 8, 9, 0, 0)
    _delta_util.seed(ws / "data", "curated", "extrato_arquivos", pl.DataFrame({
        "arquivo": ["manual.pdf", NOME_A], "account_uuid": [CONTA, CONTA], "emitido_em": [emitido, emitido],
        "n_lancamentos": [3, 4], "n_saldos": [3, 3], "linhas_ignoradas": [1, 0]}))
    _delta_util.seed(ws / "data", "curated", "extrato", pl.DataFrame({
        "arquivo": ["manual.pdf"], "dia": [date(2026, 6, 8)]}))
    _delta_util.seed(ws / "data", "curated", "extrato_saldos", pl.DataFrame({
        "arquivo": ["manual.pdf"], "dia": [date(2026, 6, 4)]}))
    assert cliente.get("/extratos").json() == {"arquivos": [
        {"arquivo": NOME_A, "periodo": {"de": "2026-05-31", "ate": "2026-06-05"}, "emitido_em": "2026-06-08T09:00:00",
         "n_lancamentos": 4, "n_saldos": 3, "linhas_ignoradas": 0},
        {"arquivo": "manual.pdf", "periodo": {"de": "2026-06-04", "ate": "2026-06-08"},
         "emitido_em": "2026-06-08T09:00:00", "n_lancamentos": 3, "n_saldos": 3, "linhas_ignoradas": 1},
    ]}


def test_sem_segredo_recusa(ws):
    with TestClient(backend.app) as c:
        assert c.get("/extratos").status_code == 403
        assert c.post("/extratos", content=_pdf(B)).status_code == 403
