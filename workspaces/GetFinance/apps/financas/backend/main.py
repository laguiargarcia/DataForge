"""Backend do app Finanças (df-cns.8): edita as tabelas do data/edicao.db e dispara o reclassificar.
Também marca um intervalo da conferência pelo saldo como divergência explicada (df-y26v).

Roda como processo próprio, que o DataForge sobe e para o qual encaminha /apps/<ws>/financas/api/...
(docs/workspace-layout.md, "Writing a backend"). Salvar grava direto no banco oficial, sem rascunho;
as validações são as do edicao.py. O curated é só lido: para conferir o id de uma correção e contar
o efeito de uma regra. Aplicar só pede o job `reclassificar` à API do DataForge, que o põe na fila
do workspace.

Recebe também o extrato do banco em PDF (df-7xk9.2): confere com o mesmo leitor da task `extrato`
(scripts/report/extrato.py), guarda em data/extratos com nome pelo período e pede o job `extrato`, que relê
a pasta e refaz o veredito. Nenhuma recusa diz valor, conta ou o nome do arquivo enviado.
"""
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx
import polars as pl
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from pypdf import PdfReader

from dataforge import delta_pool
from dataforge.app_guard import AppTokenMiddleware

# O código do report mora no workspace: o mesmo que o ETL roda, sem cópia.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "report"))
import edicao
import extrato
from classificacao import efeito_da_regra

JOB_APLICAR = "reclassificar"
JOB_EXTRATO = "extrato"
LIMITE_EXTRATO = 20 * 1024 * 1024   # bytes: o maior corpo que POST /extratos aceita
PREFIXO_PARCIAL, SUFIXO_PARCIAL = ".subindo-", ".parcial"   # o temporário de um upload: a task não o lê
VIDA_PARCIAL = 3600   # segundos: um temporário mais velho que isso é de um upload que morreu
# Conferir a duplicata, escolher o nome e gravar são um passo só: duas subidas ao mesmo tempo (a rota roda em
# threads) não passam ambas pela conferência nem escolhem o mesmo nome.
_TRAVA_EXTRATOS = threading.Lock()
_STATUS = {"invalido": 422, "conflito": 409, "ausente": 404}

# As recusas que cada rota pode dar, para o OpenAPI (o corpo é sempre {"detail": <mensagem>}).
_RECUSA_ESCRITA = {422: {"description": "pedido inválido"}, 409: {"description": "choca com o que já está no banco"}}
_AUSENTE = {404: {"description": "a linha não existe"}}
_SEM_DATAFORGE = {502: {"description": "a API do DataForge falhou ou não respondeu"}}
_RECUSA_EXTRATO = {
    422: {"description": "não é um extrato legível, ou a conta não casa com exatamente uma conta BANK"},
    409: {"description": "já há um extrato desta conta guardado para o mesmo período"},
    413: {"description": "arquivo grande demais"},
    502: {"description": "o PDF ficou guardado, mas a API do DataForge não pediu o job extrato"},
}

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


class Explicacao(BaseModel):
    motivo: str


def _workspace() -> Path:
    return Path(os.environ["DATAFORGE_WORKSPACE_DIR"])


def _conexao(nome: str) -> Path:
    """O caminho de uma conexão do connections.yaml do workspace (o mesmo que os scripts usam)."""
    raiz = _workspace()
    conexoes = yaml.safe_load((raiz / "connections" / "connections.yaml").read_text(encoding="utf-8"))
    return raiz / conexoes["connections"][nome]["path"]


def _tabela(conexao: str, tabela: str) -> pl.DataFrame | None:
    caminho = _conexao(conexao) / tabela
    return delta_pool.read(caminho) if (caminho / "_delta_log").exists() else None


def _curated(tabela: str) -> pl.DataFrame | None:
    return _tabela("curated", tabela)


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
        explicacoes = edicao.ler_explicacoes(conn).to_dicts()
        versao = edicao.ler_versao(conn)
    finally:
        conn.close()
    ids = _ids_vivos() or set()
    for correcao in correcoes:
        correcao["encontrada"] = correcao["transacao_id"] in ids
    aplicada_df = _curated("edicao_aplicada")
    aplicada = None if aplicada_df is None or aplicada_df.is_empty() else aplicada_df["versao"][0]
    return {"categorias": categorias, "regras": regras, "correcoes": correcoes, "explicacoes": explicacoes,
            "tipos": list(edicao.TIPOS), "versao": versao, "aplicada": aplicada,
            "pendente": versao > (aplicada or 0)}


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


# Os estados da conferência que uma marca de explicado pode cobrir (os outros já fecham).
_ESTADOS_SEM_FECHAR = ("nao_fecha", "em_aberto", "explicado")


def _instante(ate: str):
    """O fim do intervalo como a conferência o guarda (datetime UTC), ou 422 se o texto não for um instante."""
    valor = pl.Series([ate]).str.to_datetime(time_unit="us", time_zone="UTC", strict=False)[0]
    if valor is None:
        raise HTTPException(status_code=422, detail="o fim do intervalo não é um instante")
    return valor


@app.put("/explicacoes/{conta}/{ate:path}", responses=_RECUSA_ESCRITA)
def explicar(conta: str, ate: str, corpo: Explicacao) -> dict:
    """Marca o intervalo como divergência explicada, com a diferença que ele tem agora no curated."""
    instante = _instante(ate)
    conferencia = _curated("conferencia_saldo")
    linha = (None if conferencia is None else
             conferencia.filter((pl.col("account_uuid") == conta) & (pl.col("ate") == instante)))
    if linha is None or linha.is_empty():
        raise HTTPException(status_code=422, detail="intervalo não encontrado na conferência")
    intervalo = linha.row(0, named=True)
    if intervalo["estado"] not in _ESTADOS_SEM_FECHAR:
        raise HTTPException(status_code=422, detail="esse intervalo já fecha: não há divergência a explicar")
    _gravar(edicao.salvar_explicacao, conta, instante.isoformat(), intervalo["diferenca"], corpo.motivo)
    return {"conta": conta, "ate": instante.isoformat(), "diferenca": intervalo["diferenca"],
            "motivo": corpo.motivo.strip()}


@app.delete("/explicacoes/{conta}/{ate:path}", status_code=204, responses=_AUSENTE | {422: {"description": "pedido inválido"}})
def desfazer_explicacao(conta: str, ate: str) -> None:
    _gravar(edicao.excluir_explicacao, conta, _instante(ate).isoformat())


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


# --- o extrato do banco em PDF (df-7xk9.2) ---------------------------------------------------------------

async def _corpo_limitado(request: Request) -> bytes:
    """O corpo cru do pedido, ou 413 assim que ele passa de LIMITE_EXTRATO (pelo Content-Length ou lendo)."""
    recusa = HTTPException(status_code=413, detail=f"arquivo grande demais: o limite é {LIMITE_EXTRATO // 2**20} MB")
    tamanho = request.headers.get("content-length", "")
    if tamanho.isdigit() and int(tamanho) > LIMITE_EXTRATO:
        raise recusa
    partes, total = [], 0
    async for parte in request.stream():
        total += len(parte)
        if total > LIMITE_EXTRATO:
            raise recusa
        partes.append(parte)
    return b"".join(partes)


def _paginas(pdf) -> list[str]:
    return [pagina.extract_text() for pagina in PdfReader(pdf).pages]


def _ler_extrato(corpo: bytes) -> tuple[extrato.Extrato, tuple]:
    """O extrato lido do PDF e o seu período, ou 422 (sem o texto do erro, que pode trazer conteúdo do arquivo)."""
    try:
        paginas = _paginas(io.BytesIO(corpo))
    except Exception as erro:   # pypdf raises many kinds of error on bytes that are not a readable PDF
        raise HTTPException(status_code=422, detail="o arquivo não é um PDF legível") from erro
    layout = "o PDF não é um extrato do banco no layout conhecido"
    try:
        lido = extrato.ler(paginas)
        return lido, extrato.periodo(lido)
    except extrato.LayoutDesconhecido as erro:   # as mensagens do leitor só trazem datas e textos fixos
        raise HTTPException(status_code=422, detail=f"{layout}: {erro}") from erro
    except ValueError as erro:   # outro ValueError (uma data de emissão impossível): sem o texto do erro
        raise HTTPException(status_code=422, detail=layout) from erro


def _conta_guardada(arquivo: Path, contas: pl.DataFrame) -> list[str] | None:
    """As contas BANK de um PDF já guardado, ou None se ele não se lê."""
    try:
        return extrato.contas_do_extrato(extrato.ler(_paginas(arquivo)), contas)
    except Exception:   # noqa: BLE001 -- a stored file that does not read is not the same account
        return None


def _dias_divergentes(lido: extrato.Extrato, account_id: str) -> list:
    contas_uuid, saldos = _curated("dim_account"), _curated("extrato_saldos")
    if contas_uuid is None or saldos is None:
        return []
    uuids = contas_uuid.filter(pl.col("pluggy_account_id") == account_id)["account_uuid"].to_list()
    return extrato.dias_divergentes(lido.saldos, saldos.filter(pl.col("account_uuid").is_in(uuids)))


def _limpar_parciais(pasta: Path) -> None:
    """Apaga os temporários de uploads que morreram antes de virar PDF (o backup zipa a pasta inteira)."""
    limite = time.time() - VIDA_PARCIAL
    for arquivo in pasta.glob(f"{PREFIXO_PARCIAL}*{SUFIXO_PARCIAL}"):
        if arquivo.stat().st_mtime < limite:
            arquivo.unlink(missing_ok=True)


def _recusar_duplicata(pasta: Path, corpo: bytes, periodo: tuple, ids: list[str], contas: pl.DataFrame) -> None:
    """409 se a pasta já tem um PDF com os mesmos bytes (qualquer nome) ou um do app do mesmo período e conta."""
    for arquivo in pasta.iterdir():
        if not arquivo.is_file() or arquivo.suffix.lower() != ".pdf":
            continue
        if arquivo.stat().st_size == len(corpo) and arquivo.read_bytes() == corpo:
            raise HTTPException(status_code=409, detail="este arquivo já está guardado")
        if extrato.periodo_do_nome(arquivo.name) == periodo and _conta_guardada(arquivo, contas) == ids:
            de, ate = periodo
            raise HTTPException(status_code=409, detail=f"já há um extrato desta conta guardado para {de} a {ate}")


def _gravar_pdf(pasta: Path, periodo: tuple, corpo: bytes) -> str:
    """Grava num temporário da mesma pasta, sem a extensão .pdf, e liga o nome escolhido a ele: a task nunca lê
    um PDF pela metade, e um nome que já existe nunca é sobrescrito (em nenhum sistema): passa ao sufixo
    seguinte. Devolve o nome gravado; o temporário não fica, nem quando a gravação falha."""
    descritor, caminho = tempfile.mkstemp(dir=pasta, prefix=PREFIXO_PARCIAL, suffix=SUFIXO_PARCIAL)
    temporario = Path(caminho)
    try:
        with os.fdopen(descritor, "wb") as saida:
            saida.write(corpo)
        ocupados = {arquivo.name for arquivo in pasta.iterdir()}
        while True:
            nome = extrato.nome_guardado(*periodo, ocupados)
            try:
                os.link(temporario, pasta / nome)
                return nome
            except FileExistsError:
                ocupados.add(nome)
    finally:
        temporario.unlink(missing_ok=True)


def _pedir_job_extrato(nome: str) -> str:
    """O execution_id do job extrato pedido, ou 502 dizendo que o PDF já ficou guardado."""
    try:
        resposta = _pedir("POST", f"/jobs/{JOB_EXTRATO}/run").json()
        return resposta["execution_id"]
    except HTTPException as erro:
        motivo = erro.detail
    except (ValueError, KeyError, TypeError):   # 2xx sem JSON ou sem a chave
        motivo = "DataForge respondeu sem execution_id"
    raise HTTPException(status_code=502, detail=f"o PDF foi guardado como {nome}, mas o job extrato não foi "
                                                f"pedido: {motivo}")


def _guardar_extrato(corpo: bytes) -> dict:
    lido, periodo = _ler_extrato(corpo)
    contas = _tabela("cleansed", "accounts")
    ids = [] if contas is None else extrato.contas_do_extrato(lido, contas)
    if len(ids) != 1:
        raise HTTPException(status_code=422, detail=f"a agência e a conta do extrato casam com {len(ids)} contas "
                                                    "BANK dos dados (precisa ser 1)")
    divergentes = _dias_divergentes(lido, ids[0])
    pasta = _conexao("extratos")
    pasta.mkdir(parents=True, exist_ok=True)
    with _TRAVA_EXTRATOS:
        _limpar_parciais(pasta)
        _recusar_duplicata(pasta, corpo, periodo, ids, contas)
        nome = _gravar_pdf(pasta, periodo, corpo)
    de, ate = periodo
    return {"arquivo": nome, "periodo": {"de": de, "ate": ate}, "n_lancamentos": lido.lancamentos.height,
            "n_saldos": lido.saldos.height, "linhas_ignoradas": lido.ignoradas, "dias_divergentes": divergentes,
            "execution_id": _pedir_job_extrato(nome)}


@app.post("/extratos", status_code=201, responses=_RECUSA_EXTRATO, openapi_extra={"requestBody": {
    "required": True, "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}})
async def subir_extrato(request: Request) -> dict:
    """O corpo é o PDF cru (Content-Type application/pdf, sem multipart)."""
    corpo = await _corpo_limitado(request)
    return await run_in_threadpool(_guardar_extrato, corpo)


@app.get("/extratos")
def listar_extratos() -> dict:
    arquivos = _curated("extrato_arquivos")
    if arquivos is None:
        return {"arquivos": []}
    com_dias = [t for t in (_curated("extrato"), _curated("extrato_saldos")) if t is not None]
    return {"arquivos": extrato.guardados(arquivos, *com_dias)}
