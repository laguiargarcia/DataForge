"""Login per person (df-8x0m.23.6.2; docs/architecture-v2/people-and-visibility.md): each module
answers only to a person who has it (403 otherwise), 401 without a valid login, GET /api/eu says who
is logged in, and a password that checked is not checked by scrypt again until its hash changes.
Two invented people: here ana has finances only and beto the list only. The finance calls are
stubbed: what is under test is who gets in, not what the route does."""
import sqlite3

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ninho.financas import aplicar, edicoes, leitura
from ninho.lista import itens
from ninho.lista.rotas import rotas as rotas_lista
from ninho.servidor import banco as banco_do_app
from ninho.servidor import dependencias, pessoas
from ninho.servidor.app import app
from tests.ninho.conftest import ANA, BETO

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
ROTAS = [("financas", *r) for r in FINANCAS] + [("lista", *r) for r in LISTA]
IDS = [f"{metodo} {caminho}" for _, metodo, caminho, *_ in ROTAS]
PESSOAS = {"ana": ANA, "beto": BETO}


@pytest.fixture
def servidor(banco, tmp_path, monkeypatch):
    """The server with ana on finances only and beto on the list only, and the finance calls stubbed."""
    with banco_do_app.aberto(banco) as conn:
        pessoas.definir_modulos(conn, "ana", ["financas"])
        pessoas.definir_modulos(conn, "beto", ["lista"])
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
    return TestClient(app)


def _pedir(servidor, banco, rota, auth):
    _, metodo, caminho, corpo, _ = rota
    if "{id}" in caminho:   # an item that exists, so the route answers its own success
        with banco_do_app.aberto(banco) as conn:
            caminho = caminho.format(id=itens.adicionar(conn, "leite", "ana")["id"])
    return servidor.request(metodo, caminho, json=corpo, auth=auth)


def test_a_lista_de_rotas_do_teste_e_a_do_servidor():
    """A new route of a module must join ROTAS, or it would escape the checks below."""
    modulos = [rota for rota in [*app.routes, *rotas_lista.routes] if isinstance(rota, APIRoute)
               and rota.path.startswith(("/api/financas/", "/api/lista/"))]
    do_servidor = sorted((rota.path, metodo) for rota in modulos for metodo in rota.methods)
    cobertas = sorted({(rota.path, metodo) for rota in modulos for _, metodo, caminho, *_ in ROTAS if metodo in rota.methods
                       and rota.path_regex.match(caminho.split("?")[0].replace("{id}", "x"))})
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
    assert r.headers["www-authenticate"] == "Basic"


def test_senha_errada_nome_desconhecido_e_sem_cabecalho_dao_o_mesmo_401(servidor):
    respostas = [servidor.get("/api/eu", auth=("ana", "senha-errada")), servidor.get("/api/eu", auth=("ninguem", ANA[1])),
                 servidor.get("/api/eu")]
    assert {(r.status_code, r.headers["www-authenticate"], r.text) for r in respostas} == {
        (401, "Basic", respostas[0].text)}


@pytest.mark.parametrize(("quem", "modulos"), [("ana", ["financas"]), ("beto", ["lista"])])
def test_eu_diz_o_nome_e_os_modulos_e_nunca_o_hash(servidor, banco, quem, modulos):
    r = servidor.get("/api/eu", auth=PESSOAS[quem])
    assert r.status_code == 200
    assert r.json() == {"nome": quem, "modulos": modulos}
    with banco_do_app.aberto(banco) as conn:
        guardado = pessoas.achar(conn, quem)[1]
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


@pytest.fixture
def conferencias(monkeypatch):
    """The scrypt checks the login made, as the stored hash each was made against (None: unknown name)."""
    chamadas = []
    original = pessoas.confere

    def contando(senha, guardado):
        chamadas.append(guardado)
        return original(senha, guardado)

    monkeypatch.setattr(pessoas, "confere", contando)
    return chamadas


@pytest.fixture
def troca_no_meio(banco, monkeypatch):
    """A password change by another process that lands right after the login's scrypt check: the
    window between reading the row and remembering or rehashing. Arm it with the new password."""
    original = pessoas.confere
    armada = []

    def confere_e_troca(senha, guardado):
        certa = original(senha, guardado)
        if armada:
            with banco_do_app.aberto(banco) as outra:   # the command, on a connection of its own
                pessoas.trocar_senha(outra, "ana", armada.pop())
        return certa

    monkeypatch.setattr(pessoas, "confere", confere_e_troca)
    return armada


def test_uma_conferencia_certa_nao_paga_o_scrypt_de_novo(servidor, conferencias):
    for nome in ("ana", "ana", " ANA ", "Ana"):   # every spelling of the name is one person, one entry
        assert servidor.get("/api/eu", auth=(nome, ANA[1])).status_code == 200
    assert len(conferencias) == 1
    assert len(dependencias._conferidas) == 1


def test_uma_senha_errada_e_conferida_toda_vez(servidor, conferencias):
    for _ in range(2):
        assert servidor.get("/api/eu", auth=("ana", "senha-errada")).status_code == 401
    assert len(conferencias) == 2


def test_depois_de_um_login_certo_outra_senha_nao_entra(servidor):
    """The memory is of the person AND the password: a right login does not open the door to any other."""
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    assert servidor.get("/api/eu", auth=("ana", "senha-errada")).status_code == 401
    assert servidor.get("/api/eu", auth=("ana", BETO[1])).status_code == 401
    assert servidor.get("/api/eu", auth=("beto", ANA[1])).status_code == 401
    assert servidor.get("/api/eu", auth=ANA).status_code == 200


@pytest.mark.parametrize("auth", [None, ("", ANA[1]), ("  ", ANA[1]), ("ana", "")])
def test_sem_nome_ou_sem_senha_e_401_sem_scrypt(servidor, conferencias, auth):
    r = servidor.get("/api/eu", auth=auth)
    assert (r.status_code, r.headers["www-authenticate"]) == (401, "Basic")
    assert conferencias == []


def test_nome_desconhecido_paga_um_scrypt_como_senha_errada(servidor, conferencias):
    assert servidor.get("/api/eu", auth=("ninguem", ANA[1])).status_code == 401
    assert conferencias == [None]


def test_troca_de_senha_durante_a_conferencia_nao_deixa_a_velha_lembrada(servidor, troca_no_meio):
    troca_no_meio.append("senha-falsa-nova-da-ana")
    assert servidor.get("/api/eu", auth=ANA).status_code == 200   # it was right when it was checked
    assert servidor.get("/api/eu", auth=ANA).status_code == 401
    assert servidor.get("/api/eu", auth=ANA).status_code == 401
    assert servidor.get("/api/eu", auth=("ana", "senha-falsa-nova-da-ana")).status_code == 200


def test_refazer_o_hash_nao_desfaz_uma_troca_de_senha_feita_no_meio(servidor, banco, troca_no_meio, monkeypatch):
    monkeypatch.setattr(pessoas, "_N", 2**5)   # ana's hash (2**4) is to be redone at this login
    troca_no_meio.append("senha-falsa-nova-da-ana")
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    assert servidor.get("/api/eu", auth=("ana", "senha-falsa-nova-da-ana")).status_code == 200
    assert servidor.get("/api/eu", auth=ANA).status_code == 401
    with banco_do_app.aberto(banco) as conn:
        assert pessoas.conferir(conn, "ana", "senha-falsa-nova-da-ana") is not None


def test_a_memoria_tem_teto_e_sai_o_mais_antigo(servidor, banco, monkeypatch):
    monkeypatch.setattr(dependencias, "LIMITE", 2)
    outros = [(f"pessoa{i}", f"senha-falsa-{i}") for i in range(3)]
    with banco_do_app.aberto(banco) as conn:
        for nome, senha in outros:
            pessoas.criar(conn, nome, senha, [])
    for auth in outros:
        assert servidor.get("/api/eu", auth=auth).status_code == 200
    assert len(dependencias._conferidas) == 2
    ids = {i for i, _ in dependencias._conferidas.values()}
    with banco_do_app.aberto(banco) as conn:
        assert ids == {pessoas.achar(conn, nome)[0]["id"] for nome, _ in outros[1:]}


@pytest.mark.parametrize("falha", [sqlite3.OperationalError("database is locked"), pessoas.Invalido("curta")])
def test_falha_ao_refazer_o_hash_nao_derruba_o_login(servidor, banco, monkeypatch, falha):
    def quebra(*_):
        raise falha

    monkeypatch.setattr(pessoas, "refazer_hash", quebra)
    monkeypatch.setattr(pessoas, "_N", 2**5)
    with banco_do_app.aberto(banco) as conn:
        antes = pessoas.achar(conn, "ana")[1]
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    with banco_do_app.aberto(banco) as conn:
        assert pessoas.achar(conn, "ana")[1] == antes


def test_pessoa_apagada_durante_o_login_nao_e_erro(servidor, banco, monkeypatch):
    original = pessoas.confere

    def confere_e_apaga(senha, guardado):
        with banco_do_app.aberto(banco) as outra:
            outra.execute("DELETE FROM pessoas WHERE nome = 'ana'")
            outra.commit()
        return original(senha, guardado)

    monkeypatch.setattr(pessoas, "confere", confere_e_apaga)
    monkeypatch.setattr(pessoas, "_N", 2**5)   # the rehash finds no row to write
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    monkeypatch.setattr(pessoas, "confere", original)
    assert servidor.get("/api/eu", auth=ANA).status_code == 401


def test_a_troca_de_senha_por_outro_processo_vale_no_pedido_seguinte(servidor, banco):
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    with banco_do_app.aberto(banco) as conn:   # the command, on a connection of its own
        pessoas.trocar_senha(conn, "ana", "senha-falsa-nova-da-ana")
    assert servidor.get("/api/eu", auth=ANA).status_code == 401
    assert servidor.get("/api/eu", auth=("ana", "senha-falsa-nova-da-ana")).status_code == 200


def test_a_memoria_das_conferencias_nao_guarda_a_senha(servidor):
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    guardado = repr(dependencias._conferidas)
    assert dependencias._conferidas
    assert ANA[1] not in guardado
    assert ANA[1].encode().hex() not in guardado
    assert all(ANA[1].encode() not in chave for chave in dependencias._conferidas)


def test_hash_abaixo_do_custo_de_hoje_e_refeito_no_login(servidor, banco, monkeypatch):
    monkeypatch.setattr(pessoas, "_N", 2**5)   # today's cost goes up; ana's hash was made at 2**4
    assert servidor.get("/api/eu", auth=ANA).status_code == 200
    with banco_do_app.aberto(banco) as conn:
        guardado = pessoas.achar(conn, "ana")[1]
        assert guardado.startswith("scrypt$32$")
        assert pessoas.conferir(conn, "ana", ANA[1]) is not None
    assert servidor.get("/api/eu", auth=ANA).status_code == 200


def test_senha_antiga_mais_curta_que_o_minimo_segue_entrando(servidor, banco, monkeypatch):
    """A hash to redo whose password is under today's minimum is kept: the login does not break."""
    with banco_do_app.aberto(banco) as conn:
        conn.execute("UPDATE pessoas SET senha_hash = ? WHERE nome = 'beto'", (pessoas.gerar_hash("curta"),))
        conn.commit()
    monkeypatch.setattr(pessoas, "_N", 2**5)
    assert servidor.get("/api/eu", auth=("beto", "curta")).status_code == 200
    with banco_do_app.aberto(banco) as conn:
        assert pessoas.achar(conn, "beto")[1].startswith("scrypt$16$")


def test_exigir_um_modulo_desconhecido_e_erro():
    with pytest.raises(ValueError, match="desconhecido"):
        dependencias.exigir("agenda")
