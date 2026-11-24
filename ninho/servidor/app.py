"""The ninho house server (ADR 0006): ``uvicorn ninho.servidor.app:app``.

Configuration comes from the environment, read on every request:
- ``NINHO_WORKSPACE``: the GetFinance workspace folder, whose ``data/curated`` holds the published tables;
- ``NINHO_SENHA``: the one password that lets anyone in, sent as the password of HTTP Basic (the user
  name is ignored). Provisional: real login is df-8x0m.23.6. Without it set, nobody gets in.

The same server hands out the built screen (``ninho/telas/dist``) at ``/``, behind the same password:
the browser asks for it once and sends it again with every call the page makes to the routes.
"""
import logging
import os
import secrets
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from ninho.financas import aplicar, edicoes, leitura

TELAS = Path(__file__).resolve().parents[1] / "telas" / "dist"
MES = r"^\d{4}-(0[1-9]|1[0-2])$"

_STATUS = {"invalido": 422, "conflito": 409, "ausente": 404}

app = FastAPI(title="ninho")
_log = logging.getLogger("ninho")
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
    if not (Path(pasta) / "workspace.yaml").is_file():
        # A wrong folder must not be answered as "no data", nor get an app database created in it.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "NINHO_WORKSPACE não é um workspace")
    return Path(pasta).resolve()


@app.get("/api/financas/transacoes", dependencies=[Depends(_entrar)])
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


@app.get("/api/financas/edicoes", dependencies=[Depends(_entrar)])
def ler_edicoes(workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """The corrections, the categories to choose from and whether there are edits not applied yet."""
    return edicoes.ler(workspace)


@app.put("/api/financas/correcoes/{transacao_id:path}", dependencies=[Depends(_entrar)])
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
            dependencies=[Depends(_entrar)])
def desfazer_correcao(transacao_id: str, workspace: Annotated[Path, Depends(_workspace)]) -> None:
    try:
        edicoes.desfazer(workspace, transacao_id)
    except edicoes.Recusa as recusa:
        raise _recusada(recusa) from recusa


def _sem_motor(erro: aplicar.SemMotor) -> HTTPException:
    """The reason goes to the server's log, not to the screen: it can carry paths of this machine."""
    _log.error("o DataForge não respondeu: %s", erro)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "o DataForge não respondeu; o motivo está no log do servidor")


@app.post("/api/financas/aplicar", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(_entrar)])
def pedir_aplicar(workspace: Annotated[Path, Depends(_workspace)]) -> dict:
    """Ask the engine to redo the categories and the numbers; answers the run to follow."""
    try:
        return aplicar.pedir(workspace)
    except aplicar.SemMotor as erro:
        raise _sem_motor(erro) from erro


@app.get("/api/financas/aplicar/{run_id}", dependencies=[Depends(_entrar)])
def andamento_aplicar(run_id: uuid.UUID, workspace: Annotated[Path, Depends(_workspace)],
                      esperar: Annotated[float, Query(ge=0, le=60)] = 0) -> dict:
    """The state of an apply run; ``esperar`` (seconds, up to 60) holds the answer until it ends."""
    try:
        return aplicar.andamento(workspace, str(run_id), esperar)
    except aplicar.Desconhecida as erro:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "execução não encontrada") from erro
    except aplicar.SemMotor as erro:
        raise _sem_motor(erro) from erro


@app.get("/{caminho:path}", dependencies=[Depends(_entrar)], include_in_schema=False)
def tela(caminho: str) -> FileResponse:
    """The built screen: its page at "/" and the files it asks for. Declared last, so every route
    above answers first; nothing outside the built folder is ever handed out."""
    pasta = TELAS.resolve()
    arquivo = (pasta / (caminho or "index.html")).resolve()
    if caminho.startswith("api/") or not arquivo.is_relative_to(pasta) or not arquivo.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "não encontrado")
    # The page names its files by the hash of their content; the page itself must be asked again.
    cabecalhos = {"Cache-Control": "no-cache"} if arquivo.name == "index.html" else None
    return FileResponse(arquivo, headers=cabecalhos)
