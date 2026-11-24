"""The ninho house server (ADR 0006): ``uvicorn ninho.servidor.app:app``.

Configuration comes from the environment, read on every request:
- ``NINHO_WORKSPACE``: the GetFinance workspace folder, whose ``data/curated`` holds the published tables;
- ``NINHO_BANCO``: the app database, a SQLite file whose folder must exist (``ninho.servidor.banco``).
  Its table ``pessoas`` holds who may log in (``ninho.servidor.pessoas``), so without it every route
  answers 503. Finances keeps its edits in the workspace, not here.

Login is per person, by HTTP Basic (``ninho.servidor.dependencias``): 401 without a valid name and
password. Each module answers only to a person who has it, 403 to anyone else: the finance routes
below need ``financas``, the list's router (``/api/lista``, ``ninho.lista.rotas``) needs ``lista``.
``GET /api/eu`` says who is logged in and which modules they have.

The same server hands out the built screen (``ninho/telas/dist``) at ``/`` to anyone logged in: the
browser asks for the login once and sends it again with every call the page makes to the routes. Only
its two icons (``/icone-64.png``, ``/icone-180.png``) are open, since Safari asks for the home-screen
icon without the login.
"""
import logging
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ninho.financas import aplicar, edicoes, leitura
from ninho.lista.rotas import rotas as rotas_lista
from ninho.servidor.dependencias import Pessoa, entrar, exigir

TELAS = Path(__file__).resolve().parents[1] / "telas" / "dist"
MES = r"^\d{4}-(0[1-9]|1[0-2])$"

_STATUS = {"invalido": 422, "conflito": 409, "ausente": 404}

# No /docs, /redoc or /openapi.json: they would answer without a login and map every route.
app = FastAPI(title="ninho", docs_url=None, redoc_url=None, openapi_url=None)
_log = logging.getLogger("ninho")
_FINANCAS = [Depends(exigir("financas"))]


def _workspace() -> Path:
    pasta = os.environ.get("NINHO_WORKSPACE")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    if not pasta:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "NINHO_WORKSPACE não definida")
    if not (Path(pasta) / "workspace.yaml").is_file():
        # A wrong folder must not be answered as "no data", nor get an app database created in it.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "NINHO_WORKSPACE não é um workspace")
    return Path(pasta).resolve()


@app.get("/api/financas/transacoes", dependencies=_FINANCAS)
def transacoes(mes: Annotated[str, Query(pattern=MES)], workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """The classified transactions of one month (``mes=AAAA-MM``), newest first."""
    ano, numero = map(int, mes.split("-"))
    try:
        linhas = leitura.transacoes_do_mes(workspace, date(ano, numero, 1))
    except leitura.TabelaAusente as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"tabela publicada ausente: {leitura.TABELA}") from erro
    return {"mes": mes, "transacoes": linhas}


class Correcao(BaseModel):
    subcategoria: str


def _recusada(recusa: edicoes.Recusa) -> HTTPException:
    return HTTPException(_STATUS[recusa.motivo], str(recusa))


@app.get("/api/financas/edicoes", dependencies=_FINANCAS)
def ler_edicoes(workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """The corrections, the categories to choose from and whether there are edits not applied yet."""
    return edicoes.ler(workspace)


@app.put("/api/financas/correcoes/{transacao_id:path}", dependencies=_FINANCAS)
def corrigir(transacao_id: str, corpo: Correcao, workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """Set the category of one transaction; the published tables change at the next "apply"."""
    try:
        edicoes.corrigir(workspace, transacao_id, corpo.subcategoria)
    except leitura.TabelaAusente as erro:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"tabela publicada ausente: {leitura.TABELA}") from erro
    except edicoes.Recusa as recusa:
        raise _recusada(recusa) from recusa
    return {"transacao_id": transacao_id, "subcategoria": corpo.subcategoria.strip()}


@app.delete("/api/financas/correcoes/{transacao_id:path}", status_code=status.HTTP_204_NO_CONTENT,
            dependencies=_FINANCAS)
def desfazer_correcao(transacao_id: str, workspace: Annotated[Path, Depends(_workspace)]) -> None:
    try:
        edicoes.desfazer(workspace, transacao_id)
    except edicoes.Recusa as recusa:
        raise _recusada(recusa) from recusa


def _sem_motor(erro: aplicar.SemMotor) -> HTTPException:
    """The reason goes to the server's log, not to the screen: it can carry paths of this machine."""
    _log.error("o DataForge não respondeu: %s", erro)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "o DataForge não respondeu; o motivo está no log do servidor")


@app.post("/api/financas/aplicar", status_code=status.HTTP_202_ACCEPTED, dependencies=_FINANCAS)
def pedir_aplicar(workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """Ask the engine to redo the categories and the numbers; answers the run to follow."""
    try:
        return aplicar.pedir(workspace)
    except aplicar.SemMotor as erro:
        raise _sem_motor(erro) from erro


@app.get("/api/financas/aplicar/{run_id}", dependencies=_FINANCAS)
def andamento_aplicar(run_id: uuid.UUID, workspace: Annotated[Path, Depends(_workspace)],
                      esperar: Annotated[float, Query(ge=0, le=60)] = 0) -> dict:
    """The state of an apply run; ``esperar`` (seconds, up to 60) holds the answer until it ends."""
    try:
        return aplicar.andamento(workspace, str(run_id), esperar)
    except aplicar.Desconhecida as erro:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "execução não encontrada") from erro
    except aplicar.SemMotor as erro:
        raise _sem_motor(erro) from erro


app.include_router(rotas_lista, dependencies=[Depends(exigir("lista"))])


@app.get("/api/eu")
def eu(pessoa: Pessoa) -> dict:
    """Who is logged in and the modules they may open (the screen's bottom bar shows only those)."""
    return {"nome": pessoa["nome"], "modulos": pessoa["modulos"]}


# The icons are only the logo (no data), and the browser asks for them without the password: Safari
# fetches the home-screen icon on its own when a page is added to the iPhone's home screen (df-8x0m.23.29).
@app.get("/icone-{lado}.png", include_in_schema=False)
def icone(lado: str) -> FileResponse:
    """An icon of the screen, open to anyone; every other file of the screen asks for the password."""
    if lado not in ("64", "180"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "não encontrado")
    return FileResponse(TELAS.resolve() / f"icone-{lado}.png")


@app.get("/{caminho:path}", dependencies=[Depends(entrar)], include_in_schema=False)
def tela(caminho: str) -> FileResponse:
    """The built screen: its page at "/" and the files it asks for. Declared last, so every route
    above answers first; nothing outside the built folder is ever handed out."""
    pasta = TELAS.resolve()
    try:
        arquivo = (pasta / (caminho or "index.html")).resolve()
    except ValueError:  # a path the file system cannot hold, such as one with a null byte
        arquivo = None
    if arquivo is None or caminho.startswith("api/") or not arquivo.is_relative_to(pasta) or not arquivo.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "não encontrado")
    # The page names its files by the hash of their content; the page itself must be asked again.
    cabecalhos = {"Cache-Control": "no-cache"} if arquivo.name == "index.html" else None
    return FileResponse(arquivo, headers=cabecalhos)
