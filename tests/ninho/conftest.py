"""Shared by the ninho tests: the GetFinance workspace after the synthetic run."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ninho.servidor.app import app

WORKSPACE = Path(__file__).resolve().parents[2] / "workspaces" / "GetFinance"
SENHA = "senha-de-teste"
AUTH = ("", SENHA)


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


@pytest.fixture
def cliente(workspace, monkeypatch):
    """The server on the test's own workspace copy, with the password set."""
    monkeypatch.setenv("NINHO_WORKSPACE", str(workspace))
    monkeypatch.setenv("NINHO_SENHA", SENHA)
    for nome in ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG"):   # never reach a job the server starts
        monkeypatch.delenv(nome, raising=False)
    return TestClient(app)
