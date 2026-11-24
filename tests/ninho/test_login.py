"""Who gets in where (df-8x0m.23.6.2; docs/architecture-v2/people-and-visibility.md): each module
answers only to a person who has it (403 otherwise), 401 without a live session, and GET /api/eu says
who is logged in. How a session opens, lives and ends is in test_sessao.py.
Two invented people: here ana has finances only and beto the list and the wishlist. The finance calls are
stubbed: what is under test is who gets in, not what the route does."""
import re

import pytest
from fastapi.routing import APIRoute

from ninho.desejos import desejos
from ninho.desejos.rotas import rotas as rotas_desejos
from ninho.financas import aplicar, edicoes, leitura
from ninho.lista import itens
from ninho.lista.rotas import rotas as rotas_lista
from ninho.servidor import banco as banco_do_app
from ninho.servidor import dependencias, pessoas
from ninho.servidor.app import app
from tests.ninho.conftest import ANA, BETO, Cliente

RUN = "00000000-0000-4000-8000-000000000000"   # fixed: a random one would give each pytest-xdist worker other test ids
# Every route of each module, as (method, path, body, the route's own success code).
FINANCAS = [("GET", "/api/financas/transacoes?mes=2026-01", None, 200),
            ("GET", "/api/financas/edicoes", None, 200),
            ("PUT", "/api/financas/correcoes/t1", {"subcategoria": "Mercado"}, 200),
            ("DELETE", "/api/financas/correcoes/t1", None, 204),
            ("POST", "/api/financas/aplicar", None, 202),
            ("GET", f"/api/financas/aplicar/{RUN}", None, 200)]
LISTA = [("GET", "/api/lista/itens", None, 200),
         ("POST", "/api/lista/itens", {"texto": "pão"}, 201),
         ("PATCH", "/api/lista/itens/{id}", {"comprado": True}, 200),
         ("DELETE", "/api/lista/itens/{id}", None, 204),
         ("POST", "/api/lista/limpar-comprados", None, 200)]
DESEJOS = [("GET", "/api/desejos", None, 200),
           ("POST", "/api/desejos", {"titulo": "bicicleta", "de_quem": None}, 201),
           ("PATCH", "/api/desejos/{desejo}", {"realizado": True}, 200),
           ("DELETE", "/api/desejos/{desejo}", None, 204),
           ("POST", "/api/desejos/{desejo}/anotacoes", {"texto": "https://exemplo.invalid/bici"}, 201),
           ("DELETE", "/api/desejos/{desejo}/anotacoes/{anotacao}", None, 204)]
ROTAS = ([("financas", *r) for r in FINANCAS] + [("lista", *r) for r in LISTA]
         + [("desejos", *r) for r in DESEJOS])
IDS = [f"{metodo} {caminho}" for _, metodo, caminho, *_ in ROTAS]
PESSOAS = {"ana": ANA, "beto": BETO}


@pytest.fixture
def servidor(banco, tmp_path, monkeypatch):
    """The server with ana on finances only and beto on the list and the wishlist, and the finance
    calls stubbed."""
    with banco_do_app.aberto(banco) as conn:
        pessoas.definir_modulos(conn, "ana", ["financas"])
        pessoas.definir_modulos(conn, "beto", ["lista", "desejos"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workspace.yaml").write_text("name: GetFinance\n", encoding="utf-8")
    monkeypatch.setenv("NINHO_WORKSPACE", str(workspace))
    monkeypatch.setattr(leitura, "transacoes_do_mes", lambda *_: [])
    monkeypatch.setattr(edicoes, "ler", lambda *_: {"correcoes": []})
    monkeypatch.setattr(edicoes, "corrigir", lambda *_: None)
    monkeypatch.setattr(edicoes, "desfazer", lambda *_: None)
    monkeypatch.setattr(aplicar, "pedir", lambda *_: {"run_id": RUN})
    monkeypatch.setattr(aplicar, "andamento", lambda *_: {"run_id": RUN, "finished_at": None})
    return Cliente()


def _pedir(servidor, banco, rota, auth):
    _, metodo, caminho, corpo, _ = rota
    if "{" in caminho:   # an item, a wish and a note that exist, so the route answers its own success
        with banco_do_app.aberto(banco) as conn:
            desejo = desejos.adicionar(conn, {"titulo": "livro", "de_quem": None, "anotacoes": ["capa dura"]}, "ana", set())
            caminho = caminho.format(id=itens.adicionar(conn, "leite", "ana")["id"], desejo=desejo["id"],
                                     anotacao=desejo["anotacoes"][0]["id"])
    return servidor.request(metodo, caminho, json=corpo, auth=auth)


def test_a_lista_de_rotas_do_teste_e_a_do_servidor():
    """A new route of a module must join ROTAS, or it would escape the checks below."""
    modulos = [rota for rota in [*app.routes, *rotas_lista.routes, *rotas_desejos.routes] if isinstance(rota, APIRoute)
               and rota.path.startswith(("/api/financas/", "/api/lista/", "/api/desejos"))]
    do_servidor = sorted((rota.path, metodo) for rota in modulos for metodo in rota.methods)
    cobertas = sorted({(rota.path, metodo) for rota in modulos for _, metodo, caminho, *_ in ROTAS if metodo in rota.methods
                       and rota.path_regex.match(re.sub(r"\{\w+\}", "x", caminho.split("?")[0]))})
    assert cobertas == do_servidor
    assert len(ROTAS) == len(do_servidor)


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
@pytest.mark.parametrize("quem", ["ana", "beto"])
def test_cada_rota_responde_so_a_quem_tem_o_modulo(servidor, banco, quem, rota):
    modulo, *_, sucesso = rota
    eu = servidor.get("/api/eu", auth=PESSOAS[quem]).json()
    r = _pedir(servidor, banco, rota, PESSOAS[quem])
    if modulo in eu["modulos"]:
        assert r.status_code == sucesso, r.text
    else:
        assert r.status_code == 403, r.text
        assert modulo in r.json()["detail"]


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
@pytest.mark.parametrize("auth", [None, ("ana", "senha-errada"), ("ninguem", ANA[1]), ("", ""), ("beto", ANA[1])])
def test_cada_rota_da_401_sem_login_valido(servidor, banco, auth, rota):
    r = _pedir(servidor, banco, rota, auth)
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers   # nothing that makes a browser show its own box


@pytest.mark.parametrize(("quem", "modulos"), [("ana", ["financas"]), ("beto", ["lista", "desejos"])])
def test_eu_diz_o_id_o_nome_e_os_modulos_e_nunca_o_hash(servidor, banco, quem, modulos):
    r = servidor.get("/api/eu", auth=PESSOAS[quem])
    assert r.status_code == 200
    with banco_do_app.aberto(banco) as conn:
        pessoa, guardado = pessoas.achar(conn, quem)
    assert r.json() == {"id": pessoa["id"], "nome": quem, "modulos": modulos}
    assert guardado not in r.text
    assert "scrypt" not in r.text


def test_eu_sem_ninho_banco_e_503(servidor, monkeypatch):
    monkeypatch.delenv("NINHO_BANCO")
    assert servidor.get("/api/eu", auth=ANA).status_code == 503


def test_modulos_trocados_valem_no_pedido_seguinte(servidor, banco):
    assert servidor.get("/api/lista/itens", auth=BETO).status_code == 200
    with banco_do_app.aberto(banco) as conn:
        pessoas.definir_modulos(conn, "beto", [])
    assert servidor.get("/api/lista/itens", auth=BETO).status_code == 403
    assert servidor.get("/api/eu", auth=BETO).json()["modulos"] == []


def test_exigir_um_modulo_desconhecido_e_erro():
    with pytest.raises(ValueError, match="desconhecido"):
        dependencias.exigir("agenda")
