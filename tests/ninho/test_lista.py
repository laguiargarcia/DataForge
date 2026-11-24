"""The shopping list (df-8x0m.23.20.1): its routes under /api/lista, behind the password, saving in
the app database named by NINHO_BANCO. Items are invented."""
import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ninho.lista import itens
from ninho.servidor.app import app
from tests.ninho.conftest import AUTH, SENHA

ROTAS = [("get", "/api/lista/itens", None), ("post", "/api/lista/itens", {"texto": "pão"}),
         ("patch", "/api/lista/itens/x", {"comprado": True}), ("delete", "/api/lista/itens/x", None),
         ("post", "/api/lista/limpar-comprados", None)]


@pytest.fixture
def banco(tmp_path):
    return tmp_path / "ninho.db"


@pytest.fixture
def lista(banco, monkeypatch):
    """The server with the password set and an app database of its own."""
    monkeypatch.setenv("NINHO_SENHA", SENHA)
    monkeypatch.setenv("NINHO_BANCO", str(banco))
    return TestClient(app)


def _adicionar(lista, texto, auth=AUTH):
    r = lista.post("/api/lista/itens", json={"texto": texto}, auth=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _textos(lista):
    return [(i["texto"], i["comprado"]) for i in lista.get("/api/lista/itens", auth=AUTH).json()["itens"]]


@pytest.mark.parametrize(("metodo", "caminho", "corpo"), ROTAS)
def test_toda_rota_pede_a_senha(lista, metodo, caminho, corpo):
    r = lista.request(metodo, caminho, json=corpo)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert lista.request(metodo, caminho, json=corpo, auth=("ana", "outra")).status_code == 401


@pytest.mark.parametrize(("metodo", "caminho", "corpo"), ROTAS)
def test_sem_ninho_banco_a_lista_e_503(lista, monkeypatch, metodo, caminho, corpo):
    monkeypatch.delenv("NINHO_BANCO")
    assert lista.request(metodo, caminho, json=corpo, auth=AUTH).status_code == 503


def test_sem_a_pasta_do_banco_e_503_e_nada_e_criado(lista, monkeypatch, tmp_path):
    monkeypatch.setenv("NINHO_BANCO", str(tmp_path / "nao-existe" / "ninho.db"))
    assert lista.get("/api/lista/itens", auth=AUTH).status_code == 503
    assert not (tmp_path / "nao-existe").exists()


def test_sem_ninho_banco_financas_segue_respondendo(cliente, monkeypatch):
    monkeypatch.delenv("NINHO_BANCO", raising=False)
    assert cliente.get("/api/financas/edicoes", auth=AUTH).status_code == 200


def test_lista_vazia_cria_o_banco(lista, banco):
    r = lista.get("/api/lista/itens", auth=AUTH)
    assert r.status_code == 200
    assert r.json() == {"itens": []}
    assert banco.is_file()


def test_adicionar_guarda_quem_adicionou_e_tira_os_espacos(lista):
    item = _adicionar(lista, "  leite  ", auth=("ana", SENHA))
    assert item["texto"] == "leite"
    assert item["comprado"] is False
    assert item["adicionado_por"] == "ana"
    assert item["comprado_em"] is None
    assert item["criado_em"].endswith("+00:00")
    assert len(item["id"]) == 32
    assert _adicionar(lista, "ovos")["adicionado_por"] == ""


@pytest.mark.parametrize("texto", ["", "   ", "x" * (itens.LIMITE + 1)])
def test_texto_vazio_ou_longo_e_422(lista, texto):
    assert lista.post("/api/lista/itens", json={"texto": texto}, auth=AUTH).status_code == 422
    assert _textos(lista) == []


def test_texto_no_limite_entra(lista):
    assert _adicionar(lista, "x" * itens.LIMITE)["texto"] == "x" * itens.LIMITE


def test_sem_texto_e_422(lista):
    assert lista.post("/api/lista/itens", json={}, auth=AUTH).status_code == 422


def test_a_ordem_poe_os_que_faltam_primeiro_mais_novos_em_cima(lista):
    pao, leite, _ovos = (_adicionar(lista, t) for t in ("pão", "leite", "ovos"))
    lista.patch(f"/api/lista/itens/{pao['id']}", json={"comprado": True}, auth=AUTH)
    lista.patch(f"/api/lista/itens/{leite['id']}", json={"comprado": True}, auth=AUTH)
    assert _textos(lista) == [("ovos", False), ("leite", True), ("pão", True)]


def test_marcar_e_desmarcar_comprado(lista):
    item = _adicionar(lista, "café")
    r = lista.patch(f"/api/lista/itens/{item['id']}", json={"comprado": True}, auth=AUTH)
    assert r.status_code == 200
    assert r.json()["comprado"] is True
    assert r.json()["comprado_em"] is not None
    r = lista.patch(f"/api/lista/itens/{item['id']}", json={"comprado": False}, auth=AUTH)
    assert r.json()["comprado"] is False
    assert r.json()["comprado_em"] is None
    assert {k: v for k, v in r.json().items() if k != "comprado_em"} == {
        k: v for k, v in item.items() if k != "comprado_em"}


def test_marcar_desconhecido_e_404(lista):
    assert lista.patch("/api/lista/itens/nao-existe", json={"comprado": True}, auth=AUTH).status_code == 404


def test_marcar_sem_comprado_e_422(lista):
    item = _adicionar(lista, "arroz")
    assert lista.patch(f"/api/lista/itens/{item['id']}", json={}, auth=AUTH).status_code == 422


def test_apagar_um_item(lista):
    item = _adicionar(lista, "feijão")
    _adicionar(lista, "sal")
    r = lista.delete(f"/api/lista/itens/{item['id']}", auth=AUTH)
    assert r.status_code == 204
    assert _textos(lista) == [("sal", False)]
    assert lista.delete(f"/api/lista/itens/{item['id']}", auth=AUTH).status_code == 404


def test_limpar_comprados_apaga_so_os_comprados(lista):
    for texto in ("maçã", "pera"):
        item = _adicionar(lista, texto)
        lista.patch(f"/api/lista/itens/{item['id']}", json={"comprado": True}, auth=AUTH)
    _adicionar(lista, "uva")
    r = lista.post("/api/lista/limpar-comprados", auth=AUTH)
    assert r.status_code == 200
    assert r.json() == {"apagados": 2}
    assert _textos(lista) == [("uva", False)]
    assert lista.post("/api/lista/limpar-comprados", auth=AUTH).json() == {"apagados": 0}


def test_o_item_sobrevive_a_reabrir_o_banco(lista, banco):
    item = _adicionar(lista, "azeite", auth=("bia", SENHA))
    conn = sqlite3.connect(banco)
    try:
        assert itens.listar(conn) == [item]
        nomes = {linha[0] for linha in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert nomes == {"lista_itens"}      # the module's tables carry its prefix
    assert _textos(TestClient(app)) == [("azeite", False)]


@pytest.mark.parametrize("codificacao", ["utf-8", "latin-1"])
def test_nome_com_acento_entra_e_fica_no_item(lista, codificacao):
    """FastAPI's own HTTP Basic reads the header as ASCII and answered 401 (df-8x0m.23.20.5)."""
    cabecalho = {"Authorization": "Basic " + base64.b64encode(f"José:{SENHA}".encode(codificacao)).decode()}
    r = lista.post("/api/lista/itens", json={"texto": "pão"}, headers=cabecalho)
    assert r.status_code == 201, r.text
    assert r.json()["adicionado_por"] == "José"


@pytest.mark.parametrize("cabecalho", ["Basic ###", "Basic " + base64.b64encode(b"sem-dois-pontos").decode(),
                                       "Bearer abc", "Basic"])
def test_cabecalho_de_login_ilegivel_pede_a_senha(lista, cabecalho):
    r = lista.get("/api/lista/itens", headers={"Authorization": cabecalho})
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Basic"
