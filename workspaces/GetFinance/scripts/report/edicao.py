"""edicao.py — as tabelas que o usuário edita (dicionário de categorias, mapeamento e correções por
transação), em SQLite, e a versão delas.

O banco fica em data/ (conexão `edicao` do connections.yaml): é ambiente, nenhum deploy o leva,
então uma edição nunca é sobrescrita e cada workspace tem as suas. O ETL só lê; quem escreve é a
carga inicial (uma vez) e o backend do app Finanças (apps/financas/backend), pelas funções de
escrita no fim deste módulo. Cada uma valida, grava e soma 1 em `versao` numa transação só; uma
gravação recusada levanta `Recusa` e não muda nada.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from classificacao import TIPOS_ENTRADA, TIPOS_SAIDA

# Os Tipos que uma categoria pode ter (df-8uck): um Tipo fora das listas faria a regra não se
# aplicar a nenhuma linha, sem aviso.
TIPOS = TIPOS_SAIDA + TIPOS_ENTRADA

T_CATEGORIAS = "categorias"
T_MAPEAMENTO = "mapeamento"
T_CORRECOES = "correcoes"
T_VERSAO = "versao"

# Colunas no banco -> colunas no DataFrame (o v3 chama a coluna de "Tipo").
COLS_DICIONARIO = {"subcategoria": "subcategoria", "categoria": "categoria", "tipo": "Tipo"}
COLS_MAPEAMENTO = {"padrao": "padrao", "subcategoria": "subcategoria"}
COLS_CORRECOES = {"transacao_id": "transacao_id", "subcategoria": "subcategoria"}

_ESQUEMA = f"""
CREATE TABLE IF NOT EXISTS {T_CATEGORIAS} (
    subcategoria TEXT PRIMARY KEY NOT NULL,
    categoria    TEXT,
    tipo         TEXT
);
CREATE TABLE IF NOT EXISTS {T_MAPEAMENTO} (
    padrao       TEXT PRIMARY KEY NOT NULL,
    subcategoria TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {T_CORRECOES} (
    transacao_id TEXT PRIMARY KEY NOT NULL,
    subcategoria TEXT NOT NULL
);
-- Uma linha só: quem grava nas tabelas acima soma 1 em `numero` na mesma transação. O classify
-- grava a versão que leu em curated.edicao_aplicada; a tela compara as duas (df-cns.8).
CREATE TABLE IF NOT EXISTS {T_VERSAO} (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    numero     INTEGER NOT NULL,
    editado_em TEXT
);
"""


def abrir(caminho: Path) -> sqlite3.Connection:
    """Abre o banco, criando o arquivo e o esquema se faltarem."""
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.executescript(_ESQUEMA)
    return conn


def _ler(conn: sqlite3.Connection, tabela: str, colunas: dict[str, str]) -> pl.DataFrame:
    sql = f"SELECT {', '.join(colunas)} FROM {tabela} ORDER BY rowid"  # noqa: S608 -- nomes vêm das constantes do módulo
    rows = conn.execute(sql).fetchall()
    return pl.DataFrame(rows, schema={alvo: pl.String for alvo in colunas.values()}, orient="row")


def ler_dicionario(conn: sqlite3.Connection) -> pl.DataFrame:
    return _ler(conn, T_CATEGORIAS, COLS_DICIONARIO)


def ler_mapeamento(conn: sqlite3.Connection) -> pl.DataFrame:
    return _ler(conn, T_MAPEAMENTO, COLS_MAPEAMENTO)


def ler_correcoes(conn: sqlite3.Connection) -> pl.DataFrame:
    return _ler(conn, T_CORRECOES, COLS_CORRECOES)


def ler_versao(conn: sqlite3.Connection) -> int:
    """Quantas gravações as tabelas editáveis já tiveram (0 antes da primeira)."""
    row = conn.execute(f"SELECT numero FROM {T_VERSAO} WHERE id = 1").fetchone()  # noqa: S608 -- tabela é constante do módulo
    return row[0] if row else 0


def ler_tabelas(conn: sqlite3.Connection) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(dicionario, mapeamento). Dicionário vazio é erro: rotear sem ele classificaria tudo pelo
    padrão do lado, em silêncio."""
    dicionario = ler_dicionario(conn)
    if dicionario.is_empty():
        raise RuntimeError(
            f"tabela {T_CATEGORIAS!r} vazia — rode carga_inicial_edicao.py neste workspace"
        )
    return dicionario, ler_mapeamento(conn)


def _inserir_se_vazia(conn: sqlite3.Connection, tabela: str, colunas: dict[str, str],
                      df: pl.DataFrame) -> int:
    if conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0] > 0:  # noqa: S608 -- tabela é constante do módulo
        return 0
    sql = f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})"  # noqa: S608 -- nomes constantes e valores por parâmetro
    conn.executemany(sql, df.select(list(colunas.values())).rows())
    return df.height


def carga_inicial(conn: sqlite3.Connection, dicionario: pl.DataFrame,
                  mapeamento: pl.DataFrame) -> tuple[int, int]:
    """Grava cada tabela só se estiver vazia (nunca sobrescreve edição). Devolve quantas linhas
    entraram em cada uma. Tudo numa transação: um erro não deixa carga pela metade."""
    with conn:
        n_dic = _inserir_se_vazia(conn, T_CATEGORIAS, COLS_DICIONARIO, dicionario)
        n_map = _inserir_se_vazia(conn, T_MAPEAMENTO, COLS_MAPEAMENTO, mapeamento)
    return n_dic, n_map


# --- escrita (backend do app) ---------------------------------------------------------------------

class Recusa(ValueError):
    """Gravação recusada, com a mensagem para a tela. `motivo`: "invalido" (o pedido em si está
    errado), "conflito" (choca com o que já está no banco) ou "ausente" (a linha não existe)."""

    def __init__(self, motivo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.motivo = motivo


_CAMPO_SUBCATEGORIA = "a subcategoria"


def _texto(valor: str | None, campo: str) -> str:
    texto = (valor or "").strip()
    if not texto:
        raise Recusa("invalido", f"{campo} não pode ficar vazio")
    return texto


def _subir_versao(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"INSERT INTO {T_VERSAO} (id, numero, editado_em) VALUES (1, 1, ?) "  # noqa: S608 -- tabela é constante do módulo
        "ON CONFLICT(id) DO UPDATE SET numero = numero + 1, editado_em = excluded.editado_em",
        (datetime.now(UTC).isoformat(timespec="seconds"),),
    )


@contextmanager
def _gravacao(conn: sqlite3.Connection) -> Iterator[None]:
    """Uma gravação: BEGIN IMMEDIATE (a checagem e a escrita não deixam outro escritor entrar no
    meio), o corpo e a versão somada; uma exceção desfaz tudo."""
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        yield
        _subir_versao(conn)


def _existe(conn: sqlite3.Connection, tabela: str, coluna: str, valor: str) -> bool:
    sql = f"SELECT 1 FROM {tabela} WHERE {coluna} = ?"  # noqa: S608 -- nomes constantes do módulo
    return conn.execute(sql, (valor,)).fetchone() is not None


def _exigir_subcategoria(conn: sqlite3.Connection, subcategoria: str) -> None:
    if not _existe(conn, T_CATEGORIAS, "subcategoria", subcategoria):
        raise Recusa("invalido", f"a subcategoria {subcategoria!r} não está no dicionário")


def salvar_categoria(conn: sqlite3.Connection, antiga: str | None, subcategoria: str,
                     categoria: str, tipo: str) -> None:
    """Cria (antiga None) ou altera a categoria `antiga`. Renomear leva junto as regras e as
    correções que apontam para ela."""
    subcategoria = _texto(subcategoria, _CAMPO_SUBCATEGORIA)
    categoria = _texto(categoria, "a categoria")
    if tipo not in TIPOS:
        raise Recusa("invalido", f"Tipo {tipo!r} fora da lista: {', '.join(TIPOS)}")
    with _gravacao(conn):
        if antiga is not None and not _existe(conn, T_CATEGORIAS, "subcategoria", antiga):
            raise Recusa("ausente", f"a subcategoria {antiga!r} não existe")
        if subcategoria != antiga and _existe(conn, T_CATEGORIAS, "subcategoria", subcategoria):
            raise Recusa("conflito", f"a subcategoria {subcategoria!r} já existe")
        if antiga is None:
            conn.execute(f"INSERT INTO {T_CATEGORIAS} (subcategoria, categoria, tipo) VALUES (?, ?, ?)",  # noqa: S608 -- tabela é constante do módulo
                         (subcategoria, categoria, tipo))
        else:
            conn.execute(f"UPDATE {T_CATEGORIAS} SET subcategoria = ?, categoria = ?, tipo = ? "  # noqa: S608 -- tabela é constante do módulo
                         "WHERE subcategoria = ?", (subcategoria, categoria, tipo, antiga))
            for tabela in (T_MAPEAMENTO, T_CORRECOES):
                conn.execute(f"UPDATE {tabela} SET subcategoria = ? WHERE subcategoria = ?",  # noqa: S608 -- tabela é constante do módulo
                             (subcategoria, antiga))


def excluir_categoria(conn: sqlite3.Connection, subcategoria: str) -> None:
    """Recusa excluir uma subcategoria que alguma regra ou correção usa."""
    with _gravacao(conn):
        if not _existe(conn, T_CATEGORIAS, "subcategoria", subcategoria):
            raise Recusa("ausente", f"a subcategoria {subcategoria!r} não existe")
        usos = []
        for tabela, nome in ((T_MAPEAMENTO, "regra(s)"), (T_CORRECOES, "correção(ões)")):
            n = conn.execute(f"SELECT COUNT(*) FROM {tabela} WHERE subcategoria = ?",  # noqa: S608 -- tabela é constante do módulo
                             (subcategoria,)).fetchone()[0]
            if n:
                usos.append(f"{n} {nome}")
        if usos:
            raise Recusa("conflito", f"a subcategoria {subcategoria!r} está em uso: {' e '.join(usos)}")
        conn.execute(f"DELETE FROM {T_CATEGORIAS} WHERE subcategoria = ?", (subcategoria,))  # noqa: S608 -- tabela é constante do módulo


def salvar_regra(conn: sqlite3.Connection, antigo: str | None, padrao: str, subcategoria: str) -> None:
    """Cria (antigo None) ou altera a regra `antigo`. O padrão é único sem diferenciar maiúsculas,
    porque o ETL compara em maiúsculas: duas regras iguais assim empatariam em silêncio."""
    if not (padrao or "").strip():
        raise Recusa("invalido", "o padrão não pode ficar vazio")
    if padrao != padrao.strip():
        raise Recusa("invalido", "o padrão não pode começar nem terminar com espaço")
    subcategoria = _texto(subcategoria, _CAMPO_SUBCATEGORIA)
    with _gravacao(conn):
        if antigo is not None and not _existe(conn, T_MAPEAMENTO, "padrao", antigo):
            raise Recusa("ausente", f"a regra {antigo!r} não existe")
        _exigir_subcategoria(conn, subcategoria)
        outros = [p for (p,) in conn.execute(f"SELECT padrao FROM {T_MAPEAMENTO}") if p != antigo]  # noqa: S608 -- tabela é constante do módulo
        igual = next((p for p in outros if p.upper() == padrao.upper()), None)
        if igual is not None:
            raise Recusa("conflito", f"já existe a regra {igual!r} (maiúsculas não contam)")
        if antigo is None:
            conn.execute(f"INSERT INTO {T_MAPEAMENTO} (padrao, subcategoria) VALUES (?, ?)",  # noqa: S608 -- tabela é constante do módulo
                         (padrao, subcategoria))
        else:
            conn.execute(f"UPDATE {T_MAPEAMENTO} SET padrao = ?, subcategoria = ? WHERE padrao = ?",  # noqa: S608 -- tabela é constante do módulo
                         (padrao, subcategoria, antigo))


def excluir_regra(conn: sqlite3.Connection, padrao: str) -> None:
    with _gravacao(conn):
        if conn.execute(f"DELETE FROM {T_MAPEAMENTO} WHERE padrao = ?", (padrao,)).rowcount == 0:  # noqa: S608 -- tabela é constante do módulo
            raise Recusa("ausente", f"a regra {padrao!r} não existe")


def salvar_correcao(conn: sqlite3.Connection, transacao_id: str, subcategoria: str) -> None:
    """Cria ou troca a correção de uma transação. Se a transação existe é o backend que confere:
    este módulo não lê o curated."""
    transacao_id = _texto(transacao_id, "o id da transação")
    subcategoria = _texto(subcategoria, _CAMPO_SUBCATEGORIA)
    with _gravacao(conn):
        _exigir_subcategoria(conn, subcategoria)
        conn.execute(
            f"INSERT INTO {T_CORRECOES} (transacao_id, subcategoria) VALUES (?, ?) "  # noqa: S608 -- tabela é constante do módulo
            "ON CONFLICT(transacao_id) DO UPDATE SET subcategoria = excluded.subcategoria",
            (transacao_id, subcategoria),
        )


def excluir_correcao(conn: sqlite3.Connection, transacao_id: str) -> None:
    with _gravacao(conn):
        if conn.execute(f"DELETE FROM {T_CORRECOES} WHERE transacao_id = ?", (transacao_id,)).rowcount == 0:  # noqa: S608 -- tabela é constante do módulo
            raise Recusa("ausente", "essa transação não tem correção")
