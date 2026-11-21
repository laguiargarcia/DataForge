"""Backend do app Finanças (df-cns.8): edita as tabelas do data/edicao.db e dispara o reclassificar.

Roda como processo próprio, que o DataForge sobe e para o qual encaminha /apps/<ws>/financas/api/...
(docs/workspace-layout.md, "Writing a backend"). Salvar grava direto no banco oficial, sem rascunho;
as validações são as do edicao.py. O curated é só lido: para conferir o id de uma correção e contar
o efeito de uma regra. Aplicar só pede o job `reclassificar` à API do DataForge, que o põe na fila
do workspace.
"""
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
import polars as pl
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from dataforge.app_guard import AppTokenMiddleware

# O código do report mora no workspace: o mesmo que o ETL roda, sem cópia.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "report"))
import edicao
from classificacao import efeito_da_regra

JOB_APLICAR = "reclassificar"
_STATUS = {"invalido": 422, "conflito": 409, "ausente": 404}

# As recusas que cada rota pode dar, para o OpenAPI (o corpo é sempre {"detail": <mensagem>}).
_RECUSA_ESCRITA = {422: {"description": "pedido inválido"}, 409: {"description": "choca com o que já está no banco"}}
_AUSENTE = {404: {"description": "a linha não existe"}}
_SEM_DATAFORGE = {502: {"description": "a API do DataForge falhou ou não respondeu"}}

app = FastAPI(title="Finanças")
app.add_middleware(AppTokenMiddleware)


class Categoria(BaseModel):
    subcategoria: str
    categoria: str
    tipo: str


class Regra(BaseModel):
    padrao: str
    subcategoria: str


class Correcao(BaseModel):
    subcategoria: str


def _workspace() -> Path:
    return Path(os.environ["DATAFORGE_WORKSPACE_DIR"])


def _conexao(nome: str) -> Path:
    """O caminho de uma conexão do connections.yaml do workspace (o mesmo que os scripts usam)."""
    raiz = _workspace()
    conexoes = yaml.safe_load((raiz / "connections" / "connections.yaml").read_text(encoding="utf-8"))
    return raiz / conexoes["connections"][nome]["path"]


def _curated(tabela: str) -> pl.DataFrame | None:
    caminho = _conexao("curated") / tabela
    return pl.read_delta(str(caminho)) if (caminho / "_delta_log").exists() else None


def _abrir():
    return edicao.abrir(_conexao("edicao"))


def _ids_vivos() -> set[str] | None:
    """Os ids que a classificação conhece (transações vivas), ou None sem curated."""
    classificada = _curated("fact_transaction_classified")
    return None if classificada is None else set(classificada["id"].to_list())


def _gravar(funcao, *args) -> None:
    conn = _abrir()
    try:
        funcao(conn, *args)
    except edicao.Recusa as recusa:
        raise HTTPException(status_code=_STATUS[recusa.motivo], detail=str(recusa)) from recusa
    finally:
        conn.close()


def _efeito(padrao: str) -> dict[str, int] | None:
    fact = _curated("fact_transaction")
    if fact is None:
        return None
    conn = _abrir()
    try:
        dicionario, mapeamento = edicao.ler_dicionario(conn), edicao.ler_mapeamento(conn)
        correcoes = edicao.ler_correcoes(conn)
    finally:
        conn.close()
    return efeito_da_regra(fact, dicionario, mapeamento, padrao, correcoes)


@app.get("/edicao")
def ler_edicao() -> dict:
    conn = _abrir()
    try:
        categorias = edicao.ler_dicionario(conn).rename({"Tipo": "tipo"}).to_dicts()
        regras = edicao.ler_mapeamento(conn).to_dicts()
        correcoes = edicao.ler_correcoes(conn).to_dicts()
        versao = edicao.ler_versao(conn)
    finally:
        conn.close()
    ids = _ids_vivos() or set()
    for correcao in correcoes:
        correcao["encontrada"] = correcao["transacao_id"] in ids
    aplicada_df = _curated("edicao_aplicada")
    aplicada = None if aplicada_df is None or aplicada_df.is_empty() else aplicada_df["versao"][0]
    return {"categorias": categorias, "regras": regras, "correcoes": correcoes, "tipos": list(edicao.TIPOS),
            "versao": versao, "aplicada": aplicada, "pendente": versao > (aplicada or 0)}


@app.post("/categorias", status_code=201, responses=_RECUSA_ESCRITA)
def criar_categoria(corpo: Categoria) -> Categoria:
    _gravar(edicao.salvar_categoria, None, corpo.subcategoria, corpo.categoria, corpo.tipo)
    return corpo


@app.put("/categorias/{subcategoria:path}", responses=_RECUSA_ESCRITA | _AUSENTE)
def alterar_categoria(subcategoria: str, corpo: Categoria) -> Categoria:
    _gravar(edicao.salvar_categoria, subcategoria, corpo.subcategoria, corpo.categoria, corpo.tipo)
    return corpo


@app.delete("/categorias/{subcategoria:path}", status_code=204, responses=_RECUSA_ESCRITA | _AUSENTE)
def excluir_categoria(subcategoria: str) -> None:
    _gravar(edicao.excluir_categoria, subcategoria)


@app.post("/regras", status_code=201, responses=_RECUSA_ESCRITA)
def criar_regra(corpo: Regra) -> dict:
    _gravar(edicao.salvar_regra, None, corpo.padrao, corpo.subcategoria)
    return {"regra": corpo, "efeito": _efeito(corpo.padrao)}


@app.put("/regras/{padrao:path}", responses=_RECUSA_ESCRITA | _AUSENTE)
def alterar_regra(padrao: str, corpo: Regra) -> dict:
    _gravar(edicao.salvar_regra, padrao, corpo.padrao, corpo.subcategoria)
    return {"regra": corpo, "efeito": _efeito(corpo.padrao)}


@app.delete("/regras/{padrao:path}", status_code=204, responses=_AUSENTE)
def excluir_regra(padrao: str) -> None:
    _gravar(edicao.excluir_regra, padrao)


@app.put("/correcoes/{transacao_id:path}", responses=_RECUSA_ESCRITA)
def corrigir(transacao_id: str, corpo: Correcao) -> dict:
    ids = _ids_vivos()
    if ids is None or transacao_id not in ids:
        raise HTTPException(status_code=422, detail="transação não encontrada nos dados")
    _gravar(edicao.salvar_correcao, transacao_id, corpo.subcategoria)
    return {"transacao_id": transacao_id, "subcategoria": corpo.subcategoria}


@app.delete("/correcoes/{transacao_id:path}", status_code=204, responses=_AUSENTE)
def desfazer_correcao(transacao_id: str) -> None:
    _gravar(edicao.excluir_correcao, transacao_id)


def _api() -> httpx.Client:
    """Cliente da API do DataForge que subiu este backend (sem proxy do ambiente)."""
    return httpx.Client(base_url=os.environ["DATAFORGE_API"], timeout=10, trust_env=False)


def _rota_ws() -> str:
    return f"/api/workspaces/{quote(os.environ['DATAFORGE_WORKSPACE'], safe='')}"


def _pedir(metodo: str, caminho: str, **kwargs) -> httpx.Response:
    try:
        with _api() as cliente:
            resposta = cliente.request(metodo, _rota_ws() + caminho, **kwargs)
    except httpx.HTTPError as erro:
        raise HTTPException(status_code=502, detail=f"DataForge não respondeu: {erro}") from erro
    if resposta.is_error:
        raise HTTPException(status_code=502, detail=f"DataForge respondeu {resposta.status_code}")
    return resposta


@app.post("/aplicar", status_code=202, responses=_SEM_DATAFORGE)
def aplicar() -> dict:
    """Pede uma execução do reclassificar; ela espera na fila se outro job do workspace roda."""
    return {"execution_id": _pedir("POST", f"/jobs/{JOB_APLICAR}/run").json()["execution_id"]}


@app.get("/aplicar/{execution_id}", responses=_SEM_DATAFORGE | {404: {"description": "execução não encontrada"}})
def andamento(execution_id: str) -> dict:
    itens = _pedir("GET", "/executions", params={"limit": 100}).json()["items"]
    execucao = next((i for i in itens if i["run_id"] == execution_id), None)
    if execucao is None:
        raise HTTPException(status_code=404, detail="execução não encontrada")
    return {"status": execucao["status"], "reason": execucao.get("reason")}
