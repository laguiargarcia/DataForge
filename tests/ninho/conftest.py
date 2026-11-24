"""Shared by the ninho tests: the GetFinance workspace after the synthetic run, and an app database
with two invented people: ana, with every module, and beto, with the list and the wishlist."""
import os
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninho.servidor import banco as banco_do_app
from ninho.servidor import dependencias, pessoas
from ninho.servidor.app import app

WORKSPACE = Path(__file__).resolve().parents[2] / "workspaces" / "GetFinance"
ANA = ("ana", "senha-falsa-da-ana")
BETO = ("beto", "senha-falsa-do-beto")
AUTH = ANA
MODULOS = {"ana": ["financas", "lista", "desejos"], "beto": ["lista", "desejos"]}


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


@pytest.fixture(autouse=True)
def memoria_vazia(monkeypatch):
    """The server's memory of logins that passed starts empty in each test."""
    monkeypatch.setattr(dependencias, "_conferidas", OrderedDict())


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
    return TestClient(app)
