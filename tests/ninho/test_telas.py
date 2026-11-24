"""The screen of the skeleton (df-8x0m.23.13.5): the server hands out the built page of ninho/telas
and its files, behind the same login as the routes, so the browser asks once and reuses it. Anyone
logged in gets the screen, whatever their modules."""
import re

import pytest
from fastapi.testclient import TestClient

from ninho.servidor import app as servidor
from tests.ninho.conftest import AUTH, BETO


@pytest.fixture
def telas(tmp_path, banco, monkeypatch):
    """The server with a small built screen of its own, and a file beside it that must stay out."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><p>tela</p>", encoding="utf-8")
    (dist / "assets" / "tela.js").write_text("console.log('tela')", encoding="utf-8")
    (tmp_path / "segredo.txt").write_text("fora da tela", encoding="utf-8")
    monkeypatch.setattr(servidor, "TELAS", dist)
    return TestClient(servidor.app)


@pytest.mark.parametrize("caminho", ["/", "/assets/tela.js"])
def test_a_tela_pede_a_senha_e_o_navegador_sabe_pedir(telas, caminho):
    r = telas.get(caminho)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize("caminho", ["/", "/assets/tela.js"])
def test_a_tela_abre_para_quem_nao_tem_financas(telas, caminho):
    assert telas.get(caminho, auth=BETO).status_code == 200


def test_a_pagina_vem_na_raiz_sem_cache(telas):
    r = telas.get("/", auth=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<p>tela</p>" in r.text
    assert r.headers["cache-control"] == "no-cache"   # a new build is seen at the next load


def test_os_arquivos_da_tela_vem_com_o_tipo_certo(telas):
    r = telas.get("/assets/tela.js", auth=AUTH)
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


@pytest.mark.parametrize("caminho", ["/assets/nao-existe.js", "/assets", "/%2E%2E/segredo.txt",
                                     "/assets/%2E%2E/%2E%2E/segredo.txt", "/api/financas/nao-existe"])
def test_fora_dos_arquivos_da_tela_e_404(telas, caminho):
    r = telas.get(caminho, auth=AUTH)
    assert r.status_code == 404
    assert "fora da tela" not in r.text


def test_caminho_com_byte_nulo_e_404(telas):
    assert telas.get("/a%00b", auth=AUTH).status_code == 404


@pytest.mark.parametrize("caminho", ["/docs", "/redoc", "/openapi.json"])
def test_o_mapa_das_rotas_nao_e_servido(telas, caminho):
    """FastAPI's own pages answer without the password and list every route (df-d7s5)."""
    assert telas.get(caminho).status_code == 401
    assert telas.get(caminho, auth=AUTH).status_code == 404


def test_as_rotas_seguem_respondendo_antes_da_tela(telas, monkeypatch, tmp_path):
    monkeypatch.setenv("NINHO_WORKSPACE", str(tmp_path / "nao-e-workspace"))
    assert telas.get("/api/financas/edicoes", auth=AUTH).status_code == 503


def test_a_tela_commitada_tem_a_pagina_e_os_arquivos_que_ela_pede():
    pagina = (servidor.TELAS / "index.html").read_text(encoding="utf-8")
    pedidos = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', pagina)
    assert pedidos
    assert all((servidor.TELAS / pedido).is_file() for pedido in pedidos)
