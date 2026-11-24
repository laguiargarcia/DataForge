"""What the server gives every module's routes (docs/architecture-v2/server-modules.md and
people-and-visibility.md): the login of a person, the check that the person has a module, and a
connection to the app database.

- Login is HTTP Basic: the name and the password of a person of the table ``pessoas`` of
  ``NINHO_BANCO`` (``ninho.servidor.pessoas``). A wrong password, an unknown name and a missing or
  unreadable header all answer the same 401, which makes the browser ask; without ``NINHO_BANCO``
  every route answers 503.
- A module's routes answer only to a person who has that module; anyone else gets 403.
- A password checks with scrypt (about 200 ms): each check that passed is remembered in memory, so
  the next requests of the same person and password cost one read of the table. What is remembered
  is an HMAC of the person's id and the password under a key of this process, never the password,
  tied to the stored hash that was actually checked: when the hash changes (``python -m ninho
  pessoas senha``, another process) the next request checks scrypt again, and the old password
  stops working. One entry per person, at most ``LIMITE``. A request with no name or no password is
  401 at once, without a scrypt; an unknown name costs one, like a wrong password.
- A hash below today's cost is written again at login, with the same password, only while the
  stored hash is still the one checked (never over a password changed meanwhile).
"""
import base64
import hmac
import logging
import secrets
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ninho.servidor import banco, pessoas

LIMITE = 32   # people remembered at most; the oldest goes first
_SEGREDO = secrets.token_bytes(32)
_conferidas: OrderedDict[bytes, tuple[str, str]] = OrderedDict()   # HMAC of "id:senha" -> (id, hash checked)
_trava = threading.Lock()
_log = logging.getLogger("ninho")


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


def _chave(id_: str, senha: str) -> bytes:
    """Keyed by the person's id, not the name typed: every spelling of one name is one entry. The id
    has no ':', so "id:senha" is unambiguous."""
    return hmac.digest(_SEGREDO, f"{id_}:{senha}".encode(), "sha256")


def _lembrar(chave: bytes, id_: str, conferido: str) -> None:
    """Remember a check that passed against the hash <conferido>: one entry per person, the oldest
    person out past ``LIMITE``."""
    with _trava:
        for velha in [k for k, (outro, _) in _conferidas.items() if outro == id_]:
            del _conferidas[velha]
        _conferidas[chave] = (id_, conferido)
        while len(_conferidas) > LIMITE:
            _conferidas.popitem(last=False)


def _lembrada(chave: bytes, id_: str, guardado: str) -> bool:
    with _trava:
        lembrada = _conferidas.get(chave)
    return lembrada is not None and lembrada[0] == id_ and secrets.compare_digest(lembrada[1], guardado)


def _refazer(conn: sqlite3.Connection, id_: str, senha: str, conferido: str) -> str:
    """The hash that stands for <senha> after trying to keep it again at today's cost: the new one
    when it was written, else <conferido>. A password under today's minimum, a password changed
    meanwhile, the person deleted meanwhile or a database error leave the stored hash as it was and
    never fail the login."""
    try:
        return pessoas.refazer_hash(conn, id_, senha, conferido) or conferido
    except (pessoas.Invalido, sqlite3.Error):
        _log.warning("não deu para refazer o hash de uma pessoa; fica o antigo", exc_info=True)
        return conferido


def _conferir(conn: sqlite3.Connection, nome: str, senha: str) -> dict | None:
    """The person whose name and password these are, else None; scrypt only when not remembered.
    The check runs against the row read here, and only that hash is remembered."""
    if not nome.strip() or not senha:
        return None   # nothing to check: no header, or a part of it empty
    achada = pessoas.achar(conn, nome)
    if achada is None:
        pessoas.confere(senha, None)   # an unknown name costs what a wrong password costs
        return None
    pessoa, guardado = achada
    chave = _chave(pessoa["id"], senha)
    if _lembrada(chave, pessoa["id"], guardado):
        return pessoa
    if not pessoas.confere(senha, guardado):
        return None
    if pessoas.precisa_refazer(guardado):
        guardado = _refazer(conn, pessoa["id"], senha, guardado)
    _lembrar(chave, pessoa["id"], guardado)
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
