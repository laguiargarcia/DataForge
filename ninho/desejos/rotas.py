"""The routes of the wishlist under ``/api/desejos``. The server mounts this router behind the login,
for people who have the module ``desejos``, and hands each route the person calling and the people
of the house; here the calls of ``desejos`` are only translated to HTTP. Every route that answers a
wish hands the rules the person calling, so the wish comes back as that person sees it (the owner
never sees the surprise gift's mark)."""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, StrictBool

from ninho.desejos import desejos
from ninho.servidor.dependencias import Casa, Pessoa, conexao

rotas = APIRouter(prefix="/api/desejos")

Conexao = Annotated[sqlite3.Connection, Depends(conexao)]


class NovoDesejo(BaseModel, extra="forbid"):   # a misspelt field is 422, not silently ignored
    titulo: str
    de_quem: str | None
    anotacoes: list[str] = []


class Edicao(BaseModel, extra="forbid"):
    titulo: str | None = None
    de_quem: str | None = None
    realizado: StrictBool | None = None   # only JSON true or false


class NovaAnotacao(BaseModel, extra="forbid"):
    texto: str


def _ausente(mensagem: str = "desejo não encontrado") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, mensagem)


def _invalido(erro: desejos.Invalido) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(erro))


def _nomes(casa: list[dict]) -> dict[str, str]:
    return {pessoa["id"]: pessoa["nome"] for pessoa in casa}


@rotas.get("")
def listar(conn: Conexao, quem: Pessoa, casa: Casa) -> dict:
    """Every wish with its notes: the ones not fulfilled first (newest on top), then the fulfilled ones."""
    return {"desejos": desejos.listar(conn, quem["id"], _nomes(casa))}


@rotas.post("", status_code=status.HTTP_201_CREATED)
def adicionar(corpo: NovoDesejo, conn: Conexao, quem: Pessoa, casa: Casa) -> dict:
    """The new wish, with the name of the person logged in as who added it."""
    try:
        return desejos.adicionar(conn, corpo.model_dump(), quem["id"], _nomes(casa))
    except desejos.Invalido as erro:
        raise _invalido(erro) from erro


@rotas.patch("/{id_}")
def editar(id_: str, corpo: Edicao, conn: Conexao, quem: Pessoa, casa: Casa) -> dict:
    """Change the title, whose it is or whether it is fulfilled; only the fields sent change."""
    try:
        return desejos.editar(conn, id_, corpo.model_dump(exclude_unset=True), quem["id"], _nomes(casa))
    except desejos.Invalido as erro:
        raise _invalido(erro) from erro
    except desejos.Ausente as erro:
        raise _ausente() from erro


@rotas.delete("/{id_}", status_code=status.HTTP_204_NO_CONTENT)
def apagar(id_: str, conn: Conexao) -> None:
    try:
        desejos.apagar(conn, id_)
    except desejos.Ausente as erro:
        raise _ausente() from erro


@rotas.post("/{id_}/anotacoes", status_code=status.HTTP_201_CREATED)
def anotar(id_: str, corpo: NovaAnotacao, conn: Conexao, quem: Pessoa, casa: Casa) -> dict:
    """Add a note at the end of the wish; answers the wish."""
    try:
        return desejos.anotar(conn, id_, corpo.texto, quem["id"], _nomes(casa))
    except desejos.Invalido as erro:
        raise _invalido(erro) from erro
    except desejos.Ausente as erro:
        raise _ausente() from erro


@rotas.delete("/{id_}/anotacoes/{anotacao_id}", status_code=status.HTTP_204_NO_CONTENT)
def desanotar(id_: str, anotacao_id: str, conn: Conexao) -> None:
    try:
        desejos.desanotar(conn, id_, anotacao_id)
    except desejos.Ausente as erro:
        raise _ausente("anotação não encontrada") from erro


@rotas.put("/{id_}/presente")
def marcar(id_: str, conn: Conexao, quem: Pessoa, casa: Casa) -> dict:
    """"Vou dar": the person calling will give this wish of another person; answers the wish. The
    owner's own wish and a wish of both are 422, the same whether or not someone marked it."""
    try:
        return desejos.marcar(conn, id_, quem["id"], _nomes(casa))
    except desejos.Invalido as erro:
        raise _invalido(erro) from erro
    except desejos.Ausente as erro:
        raise _ausente() from erro
    except desejos.DeOutraPessoa as erro:
        raise HTTPException(status.HTTP_409_CONFLICT, str(erro)) from erro


@rotas.delete("/{id_}/presente", status_code=status.HTTP_204_NO_CONTENT)
def desmarcar(id_: str, conn: Conexao, quem: Pessoa) -> None:
    """Undo one's own "vou dar"; the owner gets the same 422 as when marking."""
    try:
        desejos.desmarcar(conn, id_, quem["id"])
    except desejos.Invalido as erro:
        raise _invalido(erro) from erro
    except desejos.Ausente as erro:
        raise _ausente() from erro
    except desejos.DeOutraPessoa as erro:
        raise HTTPException(status.HTTP_409_CONFLICT, str(erro)) from erro
