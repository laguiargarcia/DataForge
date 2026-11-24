"""What the server gives every module's routes (docs/architecture-v2/server-modules.md): the password
check, the name the person typed at login, and a connection to the app database.

- ``NINHO_SENHA``: the one password that lets anyone in, sent as the password of HTTP Basic. Without it
  set, nobody gets in. Provisional: real login is df-8x0m.23.6.
- The user name of HTTP Basic is only a label ("who added it") and may be empty.
"""
import os
import secrets
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ninho.servidor import banco

_basic = HTTPBasic(auto_error=False)


def entrar(credenciais: Annotated[HTTPBasicCredentials | None, Depends(_basic)]) -> None:
    senha = os.environ.get("NINHO_SENHA", "")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    dada = credenciais.password if credenciais else ""
    if not senha or not secrets.compare_digest(dada.encode(), senha.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "senha errada", headers={"WWW-Authenticate": "Basic"})


def usuario(credenciais: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
            _: Annotated[None, Depends(entrar)]) -> str:
    """The name typed at login, after the password was checked; empty when none was typed."""
    return credenciais.username.strip() if credenciais else ""


def conexao() -> Iterator[sqlite3.Connection]:
    """A connection to the app database for one request; 503 when ``NINHO_BANCO`` is not usable."""
    try:
        arquivo = banco.caminho()
    except banco.SemBanco as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro
    with banco.aberto(arquivo) as conn:
        yield conn
