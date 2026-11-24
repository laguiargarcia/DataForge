"""The routes of the list under ``/api/lista``. The server mounts this router behind the password;
here the calls of ``itens`` are only translated to HTTP."""
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ninho.lista import itens
from ninho.servidor.dependencias import conexao, usuario

rotas = APIRouter(prefix="/api/lista")

Conexao = Annotated[sqlite3.Connection, Depends(conexao)]


class NovoItem(BaseModel):
    texto: str


class Marca(BaseModel):
    comprado: bool


def _ausente() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "item não encontrado")


@rotas.get("/itens")
def listar(conn: Conexao) -> dict:
    """Every item: the ones to buy first (newest on top), then the bought ones."""
    return {"itens": itens.listar(conn)}


@rotas.post("/itens", status_code=status.HTTP_201_CREATED)
def adicionar(corpo: NovoItem, conn: Conexao, quem: Annotated[str, Depends(usuario)]) -> dict:
    try:
        return itens.adicionar(conn, corpo.texto, quem)
    except itens.Invalido as erro:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(erro)) from erro


@rotas.patch("/itens/{id_}")
def marcar(id_: str, corpo: Marca, conn: Conexao) -> dict:
    try:
        return itens.marcar(conn, id_, corpo.comprado)
    except itens.Ausente as erro:
        raise _ausente() from erro


@rotas.delete("/itens/{id_}", status_code=status.HTTP_204_NO_CONTENT)
def apagar(id_: str, conn: Conexao) -> None:
    try:
        itens.apagar(conn, id_)
    except itens.Ausente as erro:
        raise _ausente() from erro


@rotas.post("/limpar-comprados")
def limpar_comprados(conn: Conexao) -> dict:
    return {"apagados": itens.limpar_comprados(conn)}
