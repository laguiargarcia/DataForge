"""What the server gives every module's routes (docs/architecture-v2/server-modules.md): the password
check, the name the person typed at login, and a connection to the app database.

- ``NINHO_SENHA``: the one password that lets anyone in, sent as the password of HTTP Basic. Without it
  set, nobody gets in. Provisional: real login is df-8x0m.23.6.
- The user name of HTTP Basic is only a label ("who added it") and may be empty.
"""
import base64
import binascii
import os
import secrets
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ninho.servidor import banco


def _credenciais(request: Request) -> tuple[str, str]:
    """The user name and the password of the ``Authorization: Basic`` header, ("", "") when there is
    none or it cannot be read. Read here and not by FastAPI's ``HTTPBasic``, which decodes the header
    as ASCII and so refuses a name with an accent: browsers send UTF-8, some older ones Latin-1."""
    esquema, _, dado = request.headers.get("Authorization", "").partition(" ")
    if esquema.lower() != "basic":
        return "", ""
    try:
        bruto = base64.b64decode(dado.strip(), validate=True)
    except (ValueError, binascii.Error):
        return "", ""
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    nome, _, senha = texto.partition(":")
    return nome, senha


def entrar(credenciais: Annotated[tuple[str, str], Depends(_credenciais)]) -> None:
    senha = os.environ.get("NINHO_SENHA", "")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    if not senha or not secrets.compare_digest(credenciais[1].encode(), senha.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "senha errada", headers={"WWW-Authenticate": "Basic"})


def usuario(credenciais: Annotated[tuple[str, str], Depends(_credenciais)],
            _: Annotated[None, Depends(entrar)]) -> str:
    """The name typed at login, after the password was checked; empty when none was typed."""
    return credenciais[0].strip()


def conexao() -> Iterator[sqlite3.Connection]:
    """A connection to the app database for one request; 503 when ``NINHO_BANCO`` is not usable."""
    try:
        arquivo = banco.caminho()
    except banco.SemBanco as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro
    with banco.aberto(arquivo) as conn:
        yield conn
