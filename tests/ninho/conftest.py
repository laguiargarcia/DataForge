"""Shared by the ninho tests: the GetFinance workspace after the synthetic run, and an app database
with two invented people: ana, with every module, and beto, with the list and the wishlist."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninho.servidor import banco as banco_do_app
from ninho.servidor import pessoas, sessoes
from ninho.servidor.app import app

WORKSPACE = Path(__file__).resolve().parents[2] / "workspaces" / "GetFinance"
ANA = ("ana", "senha-falsa-da-ana")
BETO = ("beto", "senha-falsa-do-beto")
AUTH = ANA
MODULOS = {"ana": ["financas", "lista", "desejos"], "beto": ["lista", "desejos"]}


class Cliente(TestClient):
    """The server as a browser sees it, over HTTPS (the session cookie is ``Secure``). ``auth=(name,
    password)`` on a request means "asked by this person": the client logs in at ``/api/entrar`` the
    first time it sees the pair and sends that session's cookie; a pair the server refuses leaves the
    request without a session, like anyone who never logged in. Its own cookie jar is never used, so
    a request without ``auth`` carries no session."""

    def __init__(self, servidor=app):
        super().__init__(servidor, base_url="https://testserver")
        self.sessoes: dict[tuple[str, str], str | None] = {}

    def entrar(self, nome: str, senha: str) -> str | None:
        """The token of a new session of the person, or None when the server refuses the login."""
        r = super().request("POST", "/api/entrar", json={"nome": nome, "senha": senha})
        self.cookies.clear()
        return r.cookies.get(sessoes.COOKIE)

    def request(self, method, url, *, auth=None, headers=None, **kwargs):
        cabecalhos = dict(headers or {})
        if isinstance(auth, tuple):   # httpx hands its own marker when the call gives none
            if auth not in self.sessoes:
                self.sessoes[auth] = self.entrar(*auth)
            if self.sessoes[auth]:
                cabecalhos["Cookie"] = f"{sessoes.COOKIE}={self.sessoes[auth]}"
        resposta = super().request(method, url, headers=cabecalhos, **kwargs)
        self.cookies.clear()
        return resposta


def rodar_job(home: Path, job: str) -> None:
    """`dataforge run GetFinance <job>` with <home> as DATAFORGE_HOME, as a process of its own."""
    env = {k: v for k, v in os.environ.items() if k not in ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG")}
    env["DATAFORGE_HOME"] = str(home)
    result = subprocess.run([sys.executable, "-m", "dataforge", "run", "GetFinance", job],
                            cwd=home, env=env, capture_output=True, text=True, encoding="utf-8",
                            timeout=600, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


@pytest.fixture(scope="session")
def workspace_demo(tmp_path_factory):
    """A copy of the GetFinance workspace after the synthetic run, under a scratch DATAFORGE_HOME.
    Shared by the whole session: a test that writes takes its own copy of it."""
    home = tmp_path_factory.mktemp("home")
    copia = home / "workspaces" / "GetFinance"
    shutil.copytree(WORKSPACE, copia,
                    ignore=shutil.ignore_patterns("data", "tests", "apps", ".pytest_cache", "__pycache__"))
    rodar_job(home, "finance_demo")
    return copia


@pytest.fixture
def workspace(workspace_demo, tmp_path):
    """A test's own copy of the synthetic run, for tests that write edits or run a job."""
    home = tmp_path / "home"
    shutil.copytree(workspace_demo.parents[1], home, dirs_exist_ok=True)
    return home / "workspaces" / "GetFinance"


@pytest.fixture(autouse=True)
def custo_baixo(monkeypatch):
    """A cheap scrypt, so a login costs nothing; test_pessoas puts the real one back where it needs it."""
    monkeypatch.setattr(pessoas, "_N", 2**4)
    monkeypatch.setattr(pessoas, "_P", 1)


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """The app database of the test (NINHO_BANCO), with ana and beto in it."""
    arquivo = tmp_path / "ninho.db"
    with banco_do_app.aberto(arquivo) as conn:
        for nome, senha in (ANA, BETO):
            pessoas.criar(conn, nome, senha, MODULOS[nome])
    monkeypatch.setenv("NINHO_BANCO", str(arquivo))
    return arquivo


@pytest.fixture
def cliente(workspace, banco, monkeypatch):
    """The server on the test's own workspace copy, with ana and beto able to log in."""
    monkeypatch.setenv("NINHO_WORKSPACE", str(workspace))
    for nome in ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG"):   # never reach a job the server starts
        monkeypatch.delenv(nome, raising=False)
    return Cliente()
