"""The ninho house server (ADR 0006): ``uvicorn ninho.servidor.app:app``.

Configuration comes from the environment, read on every request:
- ``NINHO_WORKSPACE``: the GetFinance workspace folder, whose ``data/curated`` holds the published tables;
- ``NINHO_SENHA``: the one password that lets anyone in, sent as the password of HTTP Basic (the user
  name is ignored). Provisional: real login is df-8x0m.23.6. Without it set, nobody gets in.
"""
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ninho.financas import leitura

MES = r"^\d{4}-(0[1-9]|1[0-2])$"

app = FastAPI(title="ninho")
_basic = HTTPBasic(auto_error=False)


def _entrar(credenciais: Annotated[HTTPBasicCredentials | None, Depends(_basic)]) -> None:
    senha = os.environ.get("NINHO_SENHA", "")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    dada = credenciais.password if credenciais else ""
    if not senha or not secrets.compare_digest(dada.encode(), senha.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "senha errada", headers={"WWW-Authenticate": "Basic"})


def _workspace() -> Path:
    pasta = os.environ.get("NINHO_WORKSPACE")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    if not pasta:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "NINHO_WORKSPACE não definida")
    return Path(pasta)


@app.get("/api/financas/transacoes", dependencies=[Depends(_entrar)])
def transacoes(mes: Annotated[str, Query(pattern=MES)], workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """The classified transactions of one month (``mes=AAAA-MM``), newest first."""
    ano, numero = map(int, mes.split("-"))
    try:
        linhas = leitura.transacoes_do_mes(workspace, date(ano, numero, 1))
    except leitura.TabelaAusente as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"tabela publicada ausente: {leitura.TABELA}") from erro
    return {"mes": mes, "transacoes": linhas}
