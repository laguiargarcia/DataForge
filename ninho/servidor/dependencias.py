"""What the server gives every module's routes (docs/architecture-v2/server-modules.md and
people-and-visibility.md): the login of a person, the check that the person has a module, and a
connection to the app database.

- Login is HTTP Basic: the name and the password of a person of the table ``pessoas`` of
  ``NINHO_BANCO`` (``ninho.servidor.pessoas``). A wrong password, an unknown name and a missing or
  unreadable header all answer the same 401, which makes the browser ask; without ``NINHO_BANCO``
  every route answers 503.
- A module's routes answer only to a person who has that module; anyone else gets 403.
- A password checks with scrypt (about 200 ms): each check that passed is remembered in memory, so
  the next requests of the same name and password cost one read of the table. What is remembered is
  an HMAC of name and password under a key of this process, never the password, tied to the stored
  hash it was checked against: when the hash changes (``python -m ninho pessoas senha``, another
  process) the next request checks scrypt again, and the old password stops working.
"""
import base64
import hmac
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ninho.servidor import banco, pessoas

_SEGREDO = secrets.token_bytes(32)
_conferidas: dict[bytes, tuple[str, str]] = {}   # HMAC of "nome:senha" -> (person's id, stored hash)
_trava = threading.Lock()


def _credenciais(request: Request) -> tuple[str, str]:
    """The user name and the password of the ``Authorization: Basic`` header, ("", "") when there is
    none or it cannot be read. Read here and not by FastAPI's ``HTTPBasic``, which decodes the header
    as ASCII and so refuses a name with an accent: browsers send UTF-8, some older ones Latin-1."""
    esquema, _, dado = request.headers.get("Authorization", "").partition(" ")
    if esquema.lower() != "basic":
        return "", ""
    try:
        bruto = base64.b64decode(dado.strip(), validate=True)
    except ValueError:  # binascii.Error is one
        return "", ""
    try:
        texto = bruto.decode("utf-8")
    except UnicodeDecodeError:
        texto = bruto.decode("latin-1")
    nome, _, senha = texto.partition(":")
    return nome, senha


def conexao() -> Iterator[sqlite3.Connection]:
    """A connection to the app database for one request; 503 when ``NINHO_BANCO`` is not usable."""
    try:
        arquivo = banco.caminho()
    except banco.SemBanco as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro
    with banco.aberto(arquivo) as conn:
        yield conn


def _chave(nome: str, senha: str) -> bytes:
    """The name has no ':' (the header is split at the first one), so "nome:senha" is unambiguous."""
    return hmac.digest(_SEGREDO, f"{nome}:{senha}".encode(), "sha256")


def _lembrar(chave: bytes, id_: str, guardado: str) -> None:
    """Remember a check that passed, forgetting the ones of the same person against an older hash."""
    with _trava:
        for velha in [k for k, (outro, h) in _conferidas.items() if outro == id_ and h != guardado]:
            del _conferidas[velha]
        _conferidas[chave] = (id_, guardado)


def _conferir(conn: sqlite3.Connection, nome: str, senha: str) -> dict | None:
    """The person whose name and password these are, else None; scrypt only when not remembered."""
    chave = _chave(nome, senha)
    achada = pessoas.achar(conn, nome)
    lembrada = _conferidas.get(chave)
    if achada and lembrada and lembrada[0] == achada[0]["id"] and secrets.compare_digest(lembrada[1], achada[1]):
        return achada[0]
    pessoa = pessoas.conferir(conn, nome, senha)
    if pessoa is None:
        return None
    if pessoa.pop("precisa_refazer"):
        # The same password, kept again at today's cost; one shorter than today's minimum is
        # refused there and keeps working as it is until it is changed.
        with suppress(pessoas.Invalido):
            pessoas.trocar_senha(conn, nome, senha)
    achada = pessoas.achar(conn, nome)
    if achada:
        _lembrar(chave, achada[0]["id"], achada[1])
    return pessoa


def entrar(credenciais: Annotated[tuple[str, str], Depends(_credenciais)],
           conn: Annotated[sqlite3.Connection, Depends(conexao)]) -> dict:
    """The person logged in (``id``, ``nome``, ``modulos``, ``criada_em``, never the hash); 401 when
    the name and password are not a person's."""
    pessoa = _conferir(conn, *credenciais)
    if pessoa is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "nome ou senha errados", headers={"WWW-Authenticate": "Basic"})
    return pessoa


Pessoa = Annotated[dict, Depends(entrar)]


def exigir(modulo: str) -> Callable[[dict], dict]:
    """A dependency that lets in only a person who has <modulo>; 403 for anyone else."""
    if modulo not in pessoas.MODULOS:
        raise ValueError(f"módulo desconhecido: {modulo}")

    def com_o_modulo(pessoa: Pessoa) -> dict:
        if modulo not in pessoa["modulos"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"sem acesso ao módulo {modulo}")
        return pessoa

    return com_o_modulo
