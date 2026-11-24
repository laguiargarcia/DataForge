"""What the server gives every module's routes (docs/architecture-v2/server-modules.md and
people-and-visibility.md): the person logged in, the check that the person has a module, and a
connection to the app database.

- Login is a session (``ninho.servidor.sessoes``): the cookie ``ninho_sessao`` carries a token whose
  SHA-256 is a row of the table ``sessoes`` of ``NINHO_BANCO``. A request without a live session
  answers 401, with no ``WWW-Authenticate`` header: the browser never shows its own box, the screen
  shows its login form. There is no HTTP Basic. Without ``NINHO_BANCO`` every route answers 503.
- The person of the session is read from ``pessoas`` on every request: modules taken away stop
  answering at once, and a person deleted has no session.
- A session used pushes its own expiry forward, at most once a day; the cookie is then sent again.
- A module's routes answer only to a person who has that module; anyone else gets 403.
- The password is checked only when a session opens (``conferir``, one scrypt, about 200 ms). A hash
  below today's cost is written again then, with the same password, only while the stored hash is
  still the one checked (never over a password changed meanwhile).
"""
import logging
import sqlite3
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from ninho.servidor import banco, pessoas, sessoes

_log = logging.getLogger("ninho")


def conexao() -> Iterator[sqlite3.Connection]:
    """A connection to the app database for one request; 503 when ``NINHO_BANCO`` is not usable."""
    try:
        arquivo = banco.caminho()
    except banco.SemBanco as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(erro)) from erro
    with banco.aberto(arquivo) as conn:
        yield conn


def _refazer(conn: sqlite3.Connection, id_: str, senha: str, conferido: str) -> None:
    """Try to keep <senha> again at today's cost. A password under today's minimum, a password
    changed meanwhile, the person deleted meanwhile or a database error leave the stored hash as it
    was and never fail the login."""
    try:
        pessoas.refazer_hash(conn, id_, senha, conferido)
    except (pessoas.Invalido, sqlite3.Error):
        _log.warning("não deu para refazer o hash de uma pessoa; fica o antigo", exc_info=True)


def conferir(conn: sqlite3.Connection, nome: str, senha: str) -> dict | None:
    """The person whose name and password these are, else None. An empty name or password is None
    at once; an unknown name costs one scrypt, like a wrong password."""
    if not nome.strip() or not senha:
        return None
    achada = pessoas.achar(conn, nome)
    if achada is None:
        pessoas.confere(senha, None)
        return None
    pessoa, guardado = achada
    if not pessoas.confere(senha, guardado):
        return None
    if pessoas.precisa_refazer(guardado):
        _refazer(conn, pessoa["id"], senha, guardado)
    return pessoa


def por_cookie(response: Response, token: str) -> None:
    """Hand the session's token to the browser, to keep for as long as the session lives. Never
    readable by the page's scripts, sent only over HTTPS and only on requests the screen itself makes."""
    response.set_cookie(sessoes.COOKIE, token, max_age=int(sessoes.VIDA.total_seconds()), path="/",
                        httponly=True, secure=True, samesite="strict")


def tirar_cookie(response: Response) -> None:
    response.delete_cookie(sessoes.COOKIE, path="/", httponly=True, secure=True, samesite="strict")


def entrar(request: Request, response: Response, conn: Annotated[sqlite3.Connection, Depends(conexao)]) -> dict:
    """The person logged in (``id``, ``nome``, ``modulos``, ``criada_em``, never the hash); 401 when
    the request carries no live session of a person who still exists."""
    token = request.cookies.get(sessoes.COOKIE, "")
    usada = sessoes.usar(conn, token) if token else None
    pessoa = pessoas.por_id(conn, usada[0]) if usada else None
    if pessoa is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sem sessão: entre com nome e senha")
    if usada[1]:
        por_cookie(response, token)
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


def pessoas_da_casa(conn: Annotated[sqlite3.Connection, Depends(conexao)]) -> list[dict]:
    """Everyone who may log in, as ``{id, nome}`` in the order they were created: never the hash nor
    the modules."""
    return [{"id": pessoa["id"], "nome": pessoa["nome"]} for pessoa in pessoas.listar(conn)]


Casa = Annotated[list[dict], Depends(pessoas_da_casa)]
