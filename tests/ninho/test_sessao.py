"""The session (df-8x0m.23.31; docs/architecture-v2/people-and-visibility.md, "The session"):
POST /api/entrar trades a name and a password for a cookie, the table ``sessoes`` keeps only the
hash of its token, a session lives 90 days from its last use, and POST /api/sair, a new password or
the person's removal end it. There is no HTTP Basic. People are invented."""
import base64
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pytest

from ninho.__main__ import main
from ninho.servidor import banco as banco_do_app
from ninho.servidor import pessoas, sessoes
from tests.ninho.conftest import ANA, BETO, MODULOS, Cliente

NOVENTA_DIAS = 90 * 24 * 60 * 60


@pytest.fixture
def servidor(banco):
    return Cliente()


@pytest.fixture
def relogio(monkeypatch):
    """The clock of the sessions: ``relogio(days)`` moves it forward."""
    agora = [datetime.now(UTC)]
    monkeypatch.setattr(sessoes, "_agora", lambda: agora[0])

    def passar(**tempo):
        agora[0] += timedelta(**tempo)

    return passar


def _entrar(servidor, nome, senha):
    return servidor.post("/api/entrar", json={"nome": nome, "senha": senha})


def _biscoito(resposta) -> dict:
    """The attributes of the session cookie an answer sets, with its value under "valor"."""
    cookie = SimpleCookie(resposta.headers["set-cookie"])[sessoes.COOKIE]
    return {"valor": cookie.value} | {chave: valor for chave, valor in cookie.items() if valor}


def _eu(servidor, token):
    return servidor.get("/api/eu", headers={"Cookie": f"{sessoes.COOKIE}={token}"})


def _linhas(banco) -> list[tuple]:
    with banco_do_app.aberto(banco) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sessoes'").fetchone():
            return []   # the table is created by the first session
        return conn.execute("SELECT token_hash, pessoa_id, criada_em, usada_em, expira_em FROM sessoes").fetchall()


def test_entrar_certo_da_o_cookie_e_ele_abre_as_rotas(servidor):
    r = _entrar(servidor, *ANA)
    assert (r.status_code, r.content) == (204, b"")
    r = _eu(servidor, _biscoito(r)["valor"])
    assert r.status_code == 200
    assert r.json()["nome"] == "ana"


def test_o_cookie_e_httponly_secure_strict_e_dura_noventa_dias(servidor):
    biscoito = _biscoito(_entrar(servidor, *ANA))
    assert biscoito["httponly"] is True
    assert biscoito["secure"] is True
    assert biscoito["samesite"] == "strict"
    assert biscoito["path"] == "/"
    assert biscoito["max-age"] == str(NOVENTA_DIAS)
    assert "domain" not in biscoito
    assert len(biscoito["valor"]) >= 43   # 32 random bytes, in base64


def test_cada_entrada_e_uma_sessao_com_outro_token(servidor, banco):
    tokens = {_biscoito(_entrar(servidor, *ANA))["valor"] for _ in range(3)}
    assert len(tokens) == 3
    assert len(_linhas(banco)) == 3
    assert all(_eu(servidor, token).status_code == 200 for token in tokens)


def test_a_tabela_guarda_o_hash_do_token_e_nunca_o_token(servidor, banco):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    [(guardado, pessoa_id, criada_em, usada_em, expira_em)] = _linhas(banco)
    assert guardado == hashlib.sha256(token.encode()).hexdigest()
    with banco_do_app.aberto(banco) as conn:
        assert pessoa_id == pessoas.achar(conn, "ana")[0]["id"]
    assert criada_em == usada_em
    assert datetime.fromisoformat(expira_em) - datetime.fromisoformat(criada_em) == timedelta(days=90)
    assert token.encode() not in banco.read_bytes()
    assert ANA[1].encode() not in banco.read_bytes()


@pytest.mark.parametrize("corpo", [
    {"nome": "ana", "senha": "senha-errada"}, {"nome": "ninguem", "senha": ANA[1]}, {"nome": "beto", "senha": ANA[1]},
    {"nome": "", "senha": ANA[1]}, {"nome": "  ", "senha": ANA[1]}, {"nome": "ana", "senha": ""},
    {"nome": "ana"}, {"senha": ANA[1]}, {}])
def test_entrar_errado_e_sempre_o_mesmo_401_sem_cookie(servidor, banco, corpo):
    r = servidor.post("/api/entrar", json=corpo)
    assert (r.status_code, r.json()) == (401, {"detail": "nome ou senha errados"})
    assert "set-cookie" not in r.headers
    assert "www-authenticate" not in r.headers
    assert _linhas(banco) == []


@pytest.mark.parametrize("nome", [" ANA ", "Ana", "ana"])
def test_o_nome_entra_em_qualquer_grafia(servidor, nome):
    assert _entrar(servidor, nome, ANA[1]).status_code == 204


@pytest.mark.parametrize("corpo", [{"nome": "a" * 201, "senha": ANA[1]}, {"nome": "ana", "senha": "s" * 1001},
                                   {"nome": "ana", "senha": ANA[1] + "s" * 1000},
                                   {"nome": 1, "senha": ANA[1]}, ["ana", ANA[1]]])
def test_entrar_com_corpo_fora_da_forma_e_422_e_nao_devolve_o_que_veio(servidor, corpo):
    """FastAPI's own 422 quotes the body back, password included."""
    r = servidor.post("/api/entrar", json=corpo)
    assert (r.status_code, r.json()) == (422, {"detail": "nome e senha fora da forma esperada"})
    assert ANA[1] not in r.text


def test_o_422_das_outras_rotas_segue_dizendo_o_campo(servidor):
    r = servidor.post("/api/lista/itens", json={"texto": 5}, auth=BETO)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "texto"]


def test_entrar_sem_ninho_banco_e_503(servidor, monkeypatch):
    monkeypatch.delenv("NINHO_BANCO")
    assert _entrar(servidor, *ANA).status_code == 503


@pytest.fixture
def conferencias(monkeypatch):
    """The scrypt checks made, as the stored hash each was made against (None: unknown name)."""
    chamadas = []
    original = pessoas.confere

    def contando(senha, guardado):
        chamadas.append(guardado)
        return original(senha, guardado)

    monkeypatch.setattr(pessoas, "confere", contando)
    return chamadas


def test_a_senha_e_conferida_so_ao_entrar(servidor, conferencias):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    for _ in range(3):
        assert _eu(servidor, token).status_code == 200
    assert len(conferencias) == 1


def test_nome_desconhecido_paga_um_scrypt_como_senha_errada(servidor, conferencias):
    assert _entrar(servidor, "ninguem", ANA[1]).status_code == 401
    assert _entrar(servidor, "ana", "senha-errada").status_code == 401
    assert [guardado is None for guardado in conferencias] == [True, False]


@pytest.mark.parametrize(("nome", "senha"), [("", ANA[1]), ("  ", ANA[1]), ("ana", "")])
def test_sem_nome_ou_sem_senha_nao_paga_scrypt(servidor, conferencias, nome, senha):
    assert _entrar(servidor, nome, senha).status_code == 401
    assert conferencias == []


@pytest.mark.parametrize("cabecalho", [
    "Basic " + base64.b64encode(f"{ANA[0]}:{ANA[1]}".encode()).decode(), "Bearer abc", "Basic"])
@pytest.mark.parametrize("caminho", ["/api/eu", "/api/lista/itens", "/api/pessoas"])
def test_o_cabecalho_authorization_nao_abre_nada(servidor, cabecalho, caminho):
    """HTTP Basic left for good: the right name and password in the header are no login."""
    r = servidor.get(caminho, headers={"Authorization": cabecalho})
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers
    assert "set-cookie" not in r.headers


@pytest.mark.parametrize("token", ["", "nao-e-um-token", "a" * 43])
def test_um_cookie_que_nao_e_de_sessao_e_401(servidor, token):
    _entrar(servidor, *ANA)
    assert _eu(servidor, token).status_code == 401


def test_o_hash_guardado_no_banco_nao_serve_de_cookie(servidor, banco):
    _entrar(servidor, *ANA)
    assert _eu(servidor, _linhas(banco)[0][0]).status_code == 401


def test_sair_encerra_a_sessao_e_limpa_o_cookie(servidor, banco):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    outra = _biscoito(_entrar(servidor, *ANA))["valor"]
    r = servidor.post("/api/sair", headers={"Cookie": f"{sessoes.COOKIE}={token}"})
    assert (r.status_code, r.content) == (204, b"")
    limpo = _biscoito(r)
    assert (limpo["valor"], limpo["max-age"], limpo["path"]) == ("", "0", "/")
    assert (limpo["httponly"], limpo["secure"], limpo["samesite"]) == (True, True, "strict")
    assert _eu(servidor, token).status_code == 401
    assert _eu(servidor, outra).status_code == 200   # the other phone stays in
    assert len(_linhas(banco)) == 1


@pytest.mark.parametrize("cabecalhos", [{}, {"Cookie": f"{sessoes.COOKIE}=nao-e-um-token"}])
def test_sair_sem_sessao_tambem_e_204(servidor, cabecalhos):
    r = servidor.post("/api/sair", headers=cabecalhos)
    assert r.status_code == 204
    assert _biscoito(r)["max-age"] == "0"


def test_uma_sessao_usada_no_mesmo_dia_nao_escreve_nem_manda_o_cookie(servidor, banco, relogio):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    antes = _linhas(banco)
    relogio(hours=23, minutes=59)
    r = _eu(servidor, token)
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert _linhas(banco) == antes


def test_uma_sessao_usada_depois_de_um_dia_e_empurrada_e_o_cookie_volta(servidor, banco, relogio):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    [(_, _, criada_em, _, expira_em)] = _linhas(banco)
    relogio(days=1)
    r = _eu(servidor, token)
    assert r.status_code == 200
    biscoito = _biscoito(r)
    assert (biscoito["valor"], biscoito["max-age"]) == (token, str(NOVENTA_DIAS))
    assert (biscoito["httponly"], biscoito["secure"], biscoito["samesite"]) == (True, True, "strict")
    [(_, _, criada_depois, usada_em, expira_depois)] = _linhas(banco)
    assert criada_depois == criada_em
    assert datetime.fromisoformat(expira_depois) - datetime.fromisoformat(expira_em) == timedelta(days=1)
    assert datetime.fromisoformat(expira_depois) - datetime.fromisoformat(usada_em) == timedelta(days=90)
    assert "set-cookie" not in _eu(servidor, token).headers   # pushed once, not again the same day


def test_quem_usa_sempre_nunca_vence(servidor, relogio):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    for _ in range(5):
        relogio(days=89)
        assert _eu(servidor, token).status_code == 200


def test_a_renovacao_vale_para_qualquer_rota_com_login(servidor, relogio):
    token = _biscoito(_entrar(servidor, *BETO))["valor"]
    relogio(days=2)
    r = servidor.post("/api/lista/itens", json={"texto": "pão"}, headers={"Cookie": f"{sessoes.COOKIE}={token}"})
    assert r.status_code == 201
    assert _biscoito(r)["valor"] == token


def test_uma_sessao_parada_noventa_dias_vence_e_a_linha_some(servidor, banco, relogio):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    relogio(days=89, hours=23)
    assert _eu(servidor, token).status_code == 200   # still alive, and pushed forward
    relogio(days=90)
    r = _eu(servidor, token)
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert _linhas(banco) == []
    assert _eu(servidor, token).status_code == 401


def test_a_sessao_vale_ate_o_instante_antes_dos_noventa_dias_e_nao_no_proprio(servidor, relogio):
    antes, no_instante = (_biscoito(_entrar(servidor, *ANA))["valor"] for _ in range(2))
    relogio(days=90, microseconds=-1)
    assert _eu(servidor, antes).status_code == 200
    relogio(microseconds=1)
    assert _eu(servidor, no_instante).status_code == 401


def test_entrar_varre_as_sessoes_vencidas_de_todos(servidor, banco, relogio):
    _entrar(servidor, *ANA)
    relogio(days=30)
    viva = _biscoito(_entrar(servidor, *BETO))["valor"]
    relogio(days=60)
    _entrar(servidor, *ANA)   # the first one expires at this very instant, and goes
    assert len(_linhas(banco)) == 2
    assert _eu(servidor, viva).status_code == 200


def test_entrar_de_novo_no_mesmo_navegador_encerra_a_sessao_que_ele_tinha(servidor, banco):
    primeira = _biscoito(_entrar(servidor, *ANA))["valor"]
    do_beto = _biscoito(_entrar(servidor, *BETO))["valor"]
    r = servidor.post("/api/entrar", json={"nome": ANA[0], "senha": ANA[1]}, headers={"Cookie": f"{sessoes.COOKIE}={primeira}"})
    segunda = _biscoito(r)["valor"]
    assert (_eu(servidor, primeira).status_code, _eu(servidor, segunda).status_code) == (401, 200)
    assert _eu(servidor, do_beto).status_code == 200
    assert len(_linhas(banco)) == 2


def test_entrar_errado_nao_encerra_a_sessao_que_o_navegador_tinha(servidor):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    r = servidor.post("/api/entrar", json={"nome": "ana", "senha": "senha-errada"}, headers={"Cookie": f"{sessoes.COOKIE}={token}"})
    assert r.status_code == 401
    assert _eu(servidor, token).status_code == 200


@pytest.mark.parametrize("pedido", [
    ("DELETE", "/api/lista/itens/nao-existe", None, 404), ("POST", "/api/lista/itens", {"texto": 5}, 422),
    ("GET", "/api/financas/edicoes", None, 403)], ids=["404", "422", "403"])
def test_a_renovacao_manda_o_cookie_mesmo_quando_a_rota_responde_erro(servidor, relogio, pedido):
    """The session is pushed in the database before the route runs: the cookie must follow it."""
    token = _biscoito(_entrar(servidor, *BETO))["valor"]
    metodo, caminho, corpo, esperado = pedido
    relogio(days=2)
    r = servidor.request(metodo, caminho, json=corpo, headers={"Cookie": f"{sessoes.COOKIE}={token}"})
    assert r.status_code == esperado
    biscoito = _biscoito(r)
    assert (biscoito["valor"], biscoito["max-age"]) == (token, str(NOVENTA_DIAS))


def test_um_pedido_sem_sessao_nunca_recebe_cookie(servidor, relogio):
    _entrar(servidor, *ANA)
    relogio(days=2)
    for r in (servidor.get("/api/eu"), servidor.get("/"), _eu(servidor, "nao-e-um-token")):
        assert "set-cookie" not in r.headers


def test_a_troca_de_senha_encerra_as_sessoes_da_pessoa_e_so_as_dela(servidor, banco):
    da_ana = _biscoito(_entrar(servidor, *ANA))["valor"]
    do_beto = _biscoito(_entrar(servidor, *BETO))["valor"]
    with banco_do_app.aberto(banco) as conn:   # the command, on a connection of its own
        pessoas.trocar_senha(conn, "ana", "senha-falsa-nova-da-ana")
    assert _eu(servidor, da_ana).status_code == 401
    assert _eu(servidor, do_beto).status_code == 200
    assert _entrar(servidor, *ANA).status_code == 401
    assert _entrar(servidor, "ana", "senha-falsa-nova-da-ana").status_code == 204


def test_o_comando_sair_encerra_as_sessoes_da_pessoa(servidor, banco, capsys):
    tokens = [_biscoito(_entrar(servidor, *ANA))["valor"] for _ in range(2)]
    do_beto = _biscoito(_entrar(servidor, *BETO))["valor"]
    assert main(["pessoas", "sair", " ANA ", "--banco", str(banco)]) == 0
    assert capsys.readouterr().out == "sessões encerradas: 2\n"
    assert [_eu(servidor, token).status_code for token in tokens] == [401, 401]
    assert _eu(servidor, do_beto).status_code == 200
    assert _entrar(servidor, *ANA).status_code == 204   # the password still works


def test_o_comando_sair_de_quem_nao_existe_e_erro(banco, capsys):
    assert main(["pessoas", "sair", "ninguem", "--banco", str(banco)]) == 1
    assert "ninguém com o nome ninguem" in capsys.readouterr().err


def test_pessoa_apagada_nao_tem_sessao(servidor, banco):
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    with banco_do_app.aberto(banco) as conn:
        conn.execute("DELETE FROM pessoas WHERE nome = 'ana'")
        conn.commit()
    assert _eu(servidor, token).status_code == 401


def test_modulos_trocados_valem_na_sessao_aberta(servidor, banco):
    token = _biscoito(_entrar(servidor, *BETO))["valor"]
    assert _eu(servidor, token).json()["modulos"] == MODULOS["beto"]
    with banco_do_app.aberto(banco) as conn:
        pessoas.definir_modulos(conn, "beto", [])
    assert _eu(servidor, token).json()["modulos"] == []


def test_hash_abaixo_do_custo_de_hoje_e_refeito_ao_entrar(servidor, banco, monkeypatch):
    monkeypatch.setattr(pessoas, "_N", 2**5)   # today's cost goes up; ana's hash was made at 2**4
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    with banco_do_app.aberto(banco) as conn:
        assert pessoas.achar(conn, "ana")[1].startswith("scrypt$32$")
    assert _eu(servidor, token).status_code == 200   # the same password kept again ends no session
    assert _entrar(servidor, *ANA).status_code == 204


@pytest.mark.parametrize("refazer", [False, True])
def test_uma_troca_de_senha_no_meio_da_entrada_nao_deixa_sessao_da_velha(servidor, banco, monkeypatch, refazer):
    """A password change by another process that lands right after the login's scrypt check: the
    old password opens no session, and a hash being redone does not write over the new password."""
    original = pessoas.confere
    armada = ["senha-falsa-nova-da-ana"]

    def confere_e_troca(senha, guardado):
        certa = original(senha, guardado)
        if armada:
            with banco_do_app.aberto(banco) as outra:
                pessoas.trocar_senha(outra, "ana", armada.pop())
        return certa

    monkeypatch.setattr(pessoas, "confere", confere_e_troca)
    if refazer:
        monkeypatch.setattr(pessoas, "_N", 2**5)   # ana's hash (2**4) is to be redone at this login
    r = _entrar(servidor, *ANA)   # right when it was checked, changed before the session opened
    assert r.status_code == 401
    assert "set-cookie" not in r.headers
    assert _linhas(banco) == []
    assert _entrar(servidor, "ana", "senha-falsa-nova-da-ana").status_code == 204
    assert _entrar(servidor, *ANA).status_code == 401


@pytest.mark.parametrize("falha", [sqlite3.OperationalError("database is locked"), pessoas.Invalido("curta")])
def test_falha_ao_refazer_o_hash_nao_derruba_a_entrada(servidor, banco, monkeypatch, falha):
    def quebra(*_):
        raise falha

    monkeypatch.setattr(pessoas, "refazer_hash", quebra)
    monkeypatch.setattr(pessoas, "_N", 2**5)
    with banco_do_app.aberto(banco) as conn:
        antes = pessoas.achar(conn, "ana")[1]
    assert _entrar(servidor, *ANA).status_code == 204
    with banco_do_app.aberto(banco) as conn:
        assert pessoas.achar(conn, "ana")[1] == antes


def test_senha_antiga_mais_curta_que_o_minimo_segue_entrando(servidor, banco, monkeypatch):
    """A hash to redo whose password is under today's minimum is kept: the login does not break."""
    with banco_do_app.aberto(banco) as conn:
        conn.execute("UPDATE pessoas SET senha_hash = ? WHERE nome = 'beto'", (pessoas.gerar_hash("curta"),))
        conn.commit()
    monkeypatch.setattr(pessoas, "_N", 2**5)
    assert _entrar(servidor, "beto", "curta").status_code == 204
    with banco_do_app.aberto(banco) as conn:
        assert pessoas.achar(conn, "beto")[1].startswith("scrypt$16$")


def test_nem_a_senha_nem_o_token_vao_para_o_log(servidor, caplog):
    caplog.set_level("DEBUG")
    token = _biscoito(_entrar(servidor, *ANA))["valor"]
    _entrar(servidor, "ana", "senha-errada-da-ana")
    _eu(servidor, token)
    assert all(texto not in caplog.text for texto in (ANA[1], "senha-errada-da-ana", token))
