"""The wishlist (df-8x0m.23.22.1): its routes under /api/desejos, behind the login, saving the wishes
and their notes in the app database named by NINHO_BANCO; and GET /api/pessoas, the people of the
house a wish can be of. Wishes and people are invented; the addresses are of the reserved .invalid
domain."""
import sqlite3

import pytest

from ninho.desejos import desejos
from ninho.servidor import banco as banco_do_app
from ninho.servidor import pessoas
from tests.ninho.conftest import ANA, BETO, Cliente

LINK = "https://exemplo.invalid/bicicleta"
# Every route, as (method, path, body, the route's own success code); {desejo} and {anotacao} are
# filled with a wish and a note that exist.
ROTAS = [("GET", "/api/desejos", None, 200),
         ("POST", "/api/desejos", {"titulo": "bicicleta", "de_quem": None}, 201),
         ("PATCH", "/api/desejos/{desejo}", {"titulo": "bicicleta nova"}, 200),
         ("DELETE", "/api/desejos/{desejo}", None, 204),
         ("POST", "/api/desejos/{desejo}/anotacoes", {"texto": LINK}, 201),
         ("DELETE", "/api/desejos/{desejo}/anotacoes/{anotacao}", None, 204)]
IDS = [f"{metodo} {caminho}" for metodo, caminho, *_ in ROTAS]


@pytest.fixture
def servidor(banco):
    """The server on an app database of its own, with ana and beto both on the wishlist."""
    return Cliente()


@pytest.fixture
def ids(banco):
    """The id of each person, by name."""
    with banco_do_app.aberto(banco) as conn:
        return {pessoa["nome"]: pessoa["id"] for pessoa in pessoas.listar(conn)}


def _criar(servidor, titulo="bicicleta", de_quem=None, anotacoes=None, auth=ANA):
    corpo = {"titulo": titulo, "de_quem": de_quem} | ({"anotacoes": anotacoes} if anotacoes is not None else {})
    r = servidor.post("/api/desejos", json=corpo, auth=auth)
    assert r.status_code == 201, r.text
    return r.json()


def _todos(servidor):
    r = servidor.get("/api/desejos", auth=ANA)
    assert r.status_code == 200, r.text
    return r.json()["desejos"]


def _pedir(servidor, banco, rota, auth):
    metodo, caminho, corpo, _ = rota
    if "{" in caminho:
        with banco_do_app.aberto(banco) as conn:
            desejo = desejos.adicionar(conn, {"titulo": "livro", "de_quem": None, "anotacoes": ["capa dura"]}, "ana", set())
        caminho = caminho.format(desejo=desejo["id"], anotacao=desejo["anotacoes"][0]["id"])
    return servidor.request(metodo, caminho, json=corpo, auth=auth)


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
@pytest.mark.parametrize("auth", [ANA, BETO])
def test_cada_rota_responde_a_quem_tem_o_modulo(servidor, banco, rota, auth):
    r = _pedir(servidor, banco, rota, auth)
    assert r.status_code == rota[-1], r.text


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_cada_rota_da_403_a_quem_nao_tem_o_modulo(servidor, banco, rota):
    with banco_do_app.aberto(banco) as conn:
        pessoas.definir_modulos(conn, "beto", ["lista"])
    r = _pedir(servidor, banco, rota, BETO)
    assert r.status_code == 403, r.text
    assert "desejos" in r.json()["detail"]


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
@pytest.mark.parametrize("auth", [None, ("ana", "senha-errada")])
def test_cada_rota_da_401_sem_login(servidor, banco, rota, auth):
    r = _pedir(servidor, banco, rota, auth)
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers   # nothing that makes a browser show its own box


@pytest.mark.parametrize("rota", ROTAS, ids=IDS)
def test_sem_ninho_banco_e_503(servidor, monkeypatch, rota):
    metodo, caminho, corpo, _ = rota
    monkeypatch.delenv("NINHO_BANCO")
    assert servidor.request(metodo, caminho.format(desejo="x", anotacao="y"), json=corpo, auth=ANA).status_code == 503


def test_lista_vazia(servidor):
    assert _todos(servidor) == []


def test_criar_de_cada_pessoa_e_dos_dois(servidor, ids):
    da_ana = _criar(servidor, "tênis", ids["ana"])
    do_beto = _criar(servidor, "violão", ids["beto"], auth=BETO)
    dos_dois = _criar(servidor, "viagem", None)
    assert (da_ana["de_quem"], do_beto["de_quem"], dos_dois["de_quem"]) == (ids["ana"], ids["beto"], None)
    assert (da_ana["adicionado_por"], do_beto["adicionado_por"]) == ("ana", "beto")
    assert dos_dois == {"id": dos_dois["id"], "titulo": "viagem", "de_quem": None, "adicionado_por": "ana",
                        "realizado": False, "realizado_em": None, "criado_em": dos_dois["criado_em"], "anotacoes": []}
    assert dos_dois["criado_em"].endswith("+00:00")
    assert len(dos_dois["id"]) == 32


def test_de_quem_texto_vazio_e_dos_dois(servidor):
    assert _criar(servidor, de_quem="  ")["de_quem"] is None


@pytest.mark.parametrize("de_quem", ["ninguem", "ana", 7])
def test_de_quem_desconhecido_e_422(servidor, de_quem):
    """The name is not the id; an id of nobody of the house is refused, and nothing is saved."""
    r = servidor.post("/api/desejos", json={"titulo": "bicicleta", "de_quem": de_quem}, auth=ANA)
    assert r.status_code == 422
    assert _todos(servidor) == []


def test_de_quem_e_obrigatorio(servidor):
    assert servidor.post("/api/desejos", json={"titulo": "bicicleta"}, auth=ANA).status_code == 422


@pytest.mark.parametrize("titulo", ["", "   ", "x" * (desejos.LIMITE_TITULO + 1), "linha\nquebrada", "com\ttab"])
def test_titulo_vazio_longo_ou_com_controle_e_422(servidor, titulo):
    r = servidor.post("/api/desejos", json={"titulo": titulo, "de_quem": None}, auth=ANA)
    assert r.status_code == 422
    assert _todos(servidor) == []


def test_titulo_no_limite_entra_e_perde_os_espacos(servidor):
    assert _criar(servidor, "x" * desejos.LIMITE_TITULO)["titulo"] == "x" * desejos.LIMITE_TITULO
    assert _criar(servidor, "  livro  ")["titulo"] == "livro"


@pytest.mark.parametrize(("texto", "link"), [
    (LINK, True), ("http://exemplo.invalid", True), ("HTTPS://exemplo.invalid/a?b=c#d", True),
    (f"  {LINK}  ", True),
    ("javascript:alert(1)", False), ("ftp://exemplo.invalid/arquivo", False), ("a cor azul", False),
    (f"veja {LINK}", False), ("https://", False), ("https:///sem-host", False), ("exemplo.invalid", False),
    ("https://[::1", False)])
def test_anotacao_e_link_so_quando_e_um_endereco_http_ou_https(servidor, texto, link):
    desejo = _criar(servidor, anotacoes=[texto])
    assert desejo["anotacoes"][0]["link"] is link
    assert desejo["anotacoes"][0]["texto"] == texto.strip()


def test_anotacoes_na_ordem_em_que_foram_escritas(servidor):
    desejo = _criar(servidor, anotacoes=["primeira", LINK])
    r = servidor.post(f"/api/desejos/{desejo['id']}/anotacoes", json={"texto": "terceira"}, auth=BETO)
    assert r.status_code == 201
    assert [a["texto"] for a in r.json()["anotacoes"]] == ["primeira", LINK, "terceira"]
    assert [a["texto"] for a in _todos(servidor)[0]["anotacoes"]] == ["primeira", LINK, "terceira"]
    assert len({a["id"] for a in r.json()["anotacoes"]}) == 3


@pytest.mark.parametrize("texto", ["", "  ", "x" * (desejos.LIMITE_ANOTACAO + 1), "a\u0000b", "a\nb"])
def test_anotacao_vazia_longa_ou_com_controle_e_422(servidor, texto):
    desejo = _criar(servidor)
    assert servidor.post(f"/api/desejos/{desejo['id']}/anotacoes", json={"texto": texto}, auth=ANA).status_code == 422
    assert servidor.post("/api/desejos", json={"titulo": "t", "de_quem": None, "anotacoes": [texto]},
                         auth=ANA).status_code == 422
    assert [d["id"] for d in _todos(servidor)] == [desejo["id"]]
    assert _todos(servidor)[0]["anotacoes"] == []


def test_anotacao_no_limite_entra(servidor):
    assert _criar(servidor, anotacoes=["x" * desejos.LIMITE_ANOTACAO])["anotacoes"][0]["texto"] == "x" * desejos.LIMITE_ANOTACAO


def test_no_maximo_cinquenta_anotacoes(servidor):
    cheio = [f"nota {i}" for i in range(desejos.LIMITE_ANOTACOES)]
    assert servidor.post("/api/desejos", json={"titulo": "t", "de_quem": None, "anotacoes": [*cheio, "uma a mais"]},
                         auth=ANA).status_code == 422
    desejo = _criar(servidor, anotacoes=cheio)
    r = servidor.post(f"/api/desejos/{desejo['id']}/anotacoes", json={"texto": "uma a mais"}, auth=ANA)
    assert r.status_code == 422
    assert len(_todos(servidor)[0]["anotacoes"]) == desejos.LIMITE_ANOTACOES


def test_marcar_realizado_guarda_a_hora_e_desmarcar_limpa(servidor):
    desejo = _criar(servidor)
    r = servidor.patch(f"/api/desejos/{desejo['id']}", json={"realizado": True}, auth=ANA)
    assert r.status_code == 200
    assert r.json()["realizado"] is True
    hora = r.json()["realizado_em"]
    assert hora.endswith("+00:00")
    assert servidor.patch(f"/api/desejos/{desejo['id']}", json={"realizado": True}, auth=ANA).json()["realizado_em"] == hora
    r = servidor.patch(f"/api/desejos/{desejo['id']}", json={"realizado": False}, auth=ANA)
    assert (r.json()["realizado"], r.json()["realizado_em"]) == (False, None)
    assert r.json() == desejo


def test_mover_de_parte_e_renomear(servidor, ids):
    desejo = _criar(servidor, "tênis", ids["ana"], anotacoes=[LINK])
    r = servidor.patch(f"/api/desejos/{desejo['id']}", json={"de_quem": ids["beto"]}, auth=ANA)
    assert r.json() == desejo | {"de_quem": ids["beto"]}
    r = servidor.patch(f"/api/desejos/{desejo['id']}", json={"de_quem": None, "titulo": " tênis de corrida "}, auth=ANA)
    assert r.json() == desejo | {"de_quem": None, "titulo": "tênis de corrida"}
    assert servidor.patch(f"/api/desejos/{desejo['id']}", json={}, auth=ANA).json() == r.json()


@pytest.mark.parametrize("corpo", [{"de_quem": "ninguem"}, {"titulo": ""}, {"titulo": None}, {"realizado": None},
                                   {"titulo": "x" * (desejos.LIMITE_TITULO + 1)}, {"realizado": "talvez"}])
def test_edicao_invalida_e_422_e_nao_muda_nada(servidor, ids, corpo):
    desejo = _criar(servidor, "tênis", ids["ana"])
    assert servidor.patch(f"/api/desejos/{desejo['id']}", json=corpo, auth=ANA).status_code == 422
    assert _todos(servidor) == [desejo]


def test_ordem_poe_os_nao_realizados_primeiro_mais_novos_em_cima(servidor):
    livro, bici, _viagem = (_criar(servidor, t) for t in ("livro", "bicicleta", "viagem"))
    for desejo in (livro, bici):
        servidor.patch(f"/api/desejos/{desejo['id']}", json={"realizado": True}, auth=ANA)
    assert [(d["titulo"], d["realizado"]) for d in _todos(servidor)] == [
        ("viagem", False), ("bicicleta", True), ("livro", True)]


def test_apagar_o_desejo_apaga_as_anotacoes(servidor, banco):
    desejo = _criar(servidor, anotacoes=["uma", LINK])
    outro = _criar(servidor, "livro", anotacoes=["fica"])
    assert servidor.delete(f"/api/desejos/{desejo['id']}", auth=ANA).status_code == 204
    assert [d["id"] for d in _todos(servidor)] == [outro["id"]]
    with banco_do_app.aberto(banco) as conn:
        assert conn.execute("SELECT texto FROM desejos_anotacoes").fetchall() == [("fica",)]
    assert servidor.delete(f"/api/desejos/{desejo['id']}", auth=ANA).status_code == 404


def test_apagar_uma_anotacao(servidor):
    desejo = _criar(servidor, anotacoes=["uma", "outra"])
    outro = _criar(servidor, "livro", anotacoes=["do livro"])
    uma = desejo["anotacoes"][0]["id"]
    assert servidor.delete(f"/api/desejos/{desejo['id']}/anotacoes/{uma}", auth=BETO).status_code == 204
    assert [a["texto"] for a in _todos(servidor)[1]["anotacoes"]] == ["outra"]
    assert servidor.delete(f"/api/desejos/{desejo['id']}/anotacoes/{uma}", auth=ANA).status_code == 404
    # a note is deleted only through its own wish
    alheia = outro["anotacoes"][0]["id"]
    assert servidor.delete(f"/api/desejos/{desejo['id']}/anotacoes/{alheia}", auth=ANA).status_code == 404
    assert _todos(servidor)[0]["anotacoes"] == outro["anotacoes"]


@pytest.mark.parametrize(("metodo", "caminho", "corpo"), [
    ("PATCH", "/api/desejos/nao-existe", {"realizado": True}), ("DELETE", "/api/desejos/nao-existe", None),
    ("POST", "/api/desejos/nao-existe/anotacoes", {"texto": "x"}),
    ("DELETE", "/api/desejos/nao-existe/anotacoes/nem-esta", None)])
def test_desejo_desconhecido_e_404(servidor, metodo, caminho, corpo):
    assert servidor.request(metodo, caminho, json=corpo, auth=ANA).status_code == 404


def test_o_desejo_sobrevive_a_reabrir_o_banco(servidor, banco, ids):
    desejo = _criar(servidor, "violão", ids["beto"], anotacoes=[LINK, "cordas de nylon"], auth=BETO)
    conn = sqlite3.connect(banco)
    try:
        assert desejos.listar(conn) == [desejo]
        nomes = {linha[0] for linha in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert {nome for nome in nomes if nome not in ("pessoas", "sessoes", "lista_itens")} == {"desejos_desejos", "desejos_anotacoes"}
    assert _todos(Cliente()) == [desejo]


def test_pessoas_diz_id_e_nome_e_nunca_o_hash(servidor, banco, ids):
    for auth in (ANA, BETO):
        r = servidor.get("/api/pessoas", auth=auth)
        assert r.status_code == 200
        assert r.json() == {"pessoas": [{"id": ids["ana"], "nome": "ana"}, {"id": ids["beto"], "nome": "beto"}]}
    with banco_do_app.aberto(banco) as conn:
        hashes = [pessoas.achar(conn, nome)[1] for nome in ("ana", "beto")]
    assert all(h not in r.text for h in hashes)
    assert "scrypt" not in r.text


def test_pessoas_responde_a_quem_nao_tem_modulo_nenhum(servidor, banco):
    with banco_do_app.aberto(banco) as conn:
        pessoas.definir_modulos(conn, "beto", [])
    assert servidor.get("/api/pessoas", auth=BETO).status_code == 200


@pytest.mark.parametrize("auth", [None, ("ana", "senha-errada"), ("ninguem", ANA[1])])
def test_pessoas_sem_login_e_401(servidor, auth):
    r = servidor.get("/api/pessoas", auth=auth)
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers   # nothing that makes a browser show its own box


def test_eu_diz_o_id(servidor, ids):
    assert servidor.get("/api/eu", auth=BETO).json() == {"id": ids["beto"], "nome": "beto", "modulos": ["lista", "desejos"]}


FAMILIA = "família \U0001F468‍\U0001F469‍\U0001F467 e hífen­mole"   # ZWJ emoji and a soft hyphen


def test_emoji_com_zwj_e_hifen_mole_entram_e_voltam_iguais(servidor):
    desejo = _criar(servidor, FAMILIA, anotacoes=[FAMILIA])
    assert desejo["titulo"] == FAMILIA
    r = servidor.post(f"/api/desejos/{desejo['id']}/anotacoes", json={"texto": FAMILIA}, auth=ANA)
    assert r.status_code == 201
    assert [a["texto"] for a in r.json()["anotacoes"]] == [FAMILIA, FAMILIA]
    assert _todos(servidor)[0]["titulo"] == FAMILIA


@pytest.mark.parametrize("controle", ["\n", "\t", "\u0000", " "])
def test_controle_e_separador_de_linha_seguem_recusados(servidor, controle):
    assert servidor.post("/api/desejos", json={"titulo": f"a{controle}b", "de_quem": None}, auth=ANA).status_code == 422
    desejo = _criar(servidor)
    assert servidor.post(f"/api/desejos/{desejo['id']}/anotacoes", json={"texto": f"a{controle}b"},
                         auth=ANA).status_code == 422


@pytest.mark.parametrize(("metodo", "caminho", "corpo"), [
    ("PATCH", "/api/desejos/{desejo}", {"realizdo": True}),
    ("POST", "/api/desejos", {"titulo": "t", "de_quem": None, "anotacao": ["x"]}),
    ("POST", "/api/desejos/{desejo}/anotacoes", {"texto": "x", "link": True})])
def test_campo_desconhecido_no_corpo_e_422(servidor, metodo, caminho, corpo):
    desejo = _criar(servidor)
    r = servidor.request(metodo, caminho.format(desejo=desejo["id"]), json=corpo, auth=ANA)
    assert r.status_code == 422
    assert _todos(servidor) == [desejo]


@pytest.mark.parametrize("valor", ["true", "yes", 1, 0])
def test_realizado_so_aceita_true_ou_false(servidor, valor):
    desejo = _criar(servidor)
    assert servidor.patch(f"/api/desejos/{desejo['id']}", json={"realizado": valor}, auth=ANA).status_code == 422
    assert _todos(servidor) == [desejo]


def test_as_regras_recusam_campo_desconhecido_e_anotacoes_que_nao_sao_lista(banco):
    """The rules are called without the routes too (by the tests, a future command): they check on their own."""
    with banco_do_app.aberto(banco) as conn:
        with pytest.raises(desejos.Invalido, match="anotacoes"):
            desejos.adicionar(conn, {"titulo": "t", "de_quem": None, "anotacoes": "um texto só"}, "ana", set())
        desejo = desejos.adicionar(conn, {"titulo": "t", "de_quem": None}, "ana", set())
        with pytest.raises(desejos.Invalido, match="campo desconhecido: realizdo"):
            desejos.editar(conn, desejo["id"], {"realizdo": True}, set())
        assert desejos.listar(conn) == [desejo]
