"""The screen (df-8x0m.23.13.5): the server hands out the built page of ninho/telas and its files to
anyone, logged in or not: they hold no data and the login form is part of them (df-8x0m.23.31).
Nothing outside the built folder is handed out, and the data stays behind the session."""
import re

import pytest

from ninho.servidor import app as servidor
from tests.ninho.conftest import AUTH, Cliente


@pytest.fixture
def telas(tmp_path, banco, monkeypatch):
    """The server with a small built screen of its own, and a file beside it that must stay out."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><p>tela</p>", encoding="utf-8")
    (dist / "assets" / "tela.js").write_text("console.log('tela')", encoding="utf-8")
    for lado in ("64", "180"):
        (dist / f"icone-{lado}.png").write_bytes(b"\x89PNG icone " + lado.encode())
    (tmp_path / "segredo.txt").write_text("fora da tela", encoding="utf-8")
    monkeypatch.setattr(servidor, "TELAS", dist)
    return Cliente(servidor.app)


@pytest.mark.parametrize("caminho", ["/", "/index.html", "/assets/tela.js", "/icone-64.png", "/icone-180.png"])
def test_a_tela_abre_sem_login(telas, caminho):
    """The login form is in the page, and Safari asks for the home-screen icon without cookies
    (df-8x0m.23.29)."""
    assert telas.get(caminho).status_code == 200


def test_a_tela_abre_sem_o_banco_do_app(telas, monkeypatch):
    monkeypatch.delenv("NINHO_BANCO")
    assert telas.get("/").status_code == 200


def test_a_pagina_vem_na_raiz_sem_cache(telas):
    r = telas.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<p>tela</p>" in r.text
    assert r.headers["cache-control"] == "no-cache"   # a new build is seen at the next load


def test_os_arquivos_da_tela_vem_com_o_tipo_certo(telas):
    assert "javascript" in telas.get("/assets/tela.js").headers["content-type"]
    assert telas.get("/icone-180.png").headers["content-type"] == "image/png"


@pytest.mark.parametrize("caminho", ["/assets/nao-existe.js", "/assets", "/%2E%2E/segredo.txt",
                                     "/assets/%2E%2E/%2E%2E/segredo.txt", "/api/financas/nao-existe",
                                     "/icone-..%2Fsegredo.txt.png", "/a%00b"])
@pytest.mark.parametrize("auth", [None, AUTH])
def test_fora_dos_arquivos_da_tela_e_404(telas, caminho, auth):
    r = telas.get(caminho, auth=auth)
    assert r.status_code == 404
    assert "fora da tela" not in r.text


@pytest.mark.parametrize("caminho", ["/docs", "/redoc", "/openapi.json"])
def test_o_mapa_das_rotas_nao_e_servido(telas, caminho):
    """FastAPI's own pages would list every route to anyone (df-d7s5)."""
    assert telas.get(caminho).status_code == 404


def test_os_dados_seguem_atras_da_sessao(telas):
    assert telas.get("/api/eu").status_code == 401
    assert telas.get("/api/financas/edicoes").status_code == 401


def test_as_rotas_seguem_respondendo_antes_da_tela(telas, monkeypatch, tmp_path):
    monkeypatch.setenv("NINHO_WORKSPACE", str(tmp_path / "nao-e-workspace"))
    assert telas.get("/api/financas/edicoes", auth=AUTH).status_code == 503


def test_a_tela_commitada_tem_a_pagina_e_os_arquivos_que_ela_pede():
    pagina = (servidor.TELAS / "index.html").read_text(encoding="utf-8")
    pedidos = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', pagina)
    assert pedidos
    assert all((servidor.TELAS / pedido).is_file() for pedido in pedidos)


def test_os_icones_que_a_pagina_commitada_pede_abrem_sem_login():
    pagina = (servidor.TELAS / "index.html").read_text(encoding="utf-8")
    icones = re.findall(r'<link rel="(?:icon|apple-touch-icon)"[^>]*href="\./([^"]+)"', pagina)
    assert sorted(icones) == ["icone-180.png", "icone-64.png"]
    cliente = Cliente(servidor.app)
    assert all(cliente.get(f"/{icone}").status_code == 200 for icone in icones)
