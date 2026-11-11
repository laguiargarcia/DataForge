"""edicao.py — as tabelas que o usuário edita (dicionário de categorias e mapeamento), em SQLite.

O banco fica em data/ (conexão `edicao` do connections.yaml): é ambiente, nenhum deploy o leva,
então uma edição nunca é sobrescrita e cada workspace tem as suas. O ETL só lê; quem escreve é a
carga inicial (uma vez) e, na etapa 2 do df-cns, as telas do app.
"""
import sqlite3
from pathlib import Path

import polars as pl

T_CATEGORIAS = "categorias"
T_MAPEAMENTO = "mapeamento"

# Colunas no banco -> colunas no DataFrame (o v3 chama a coluna de "Tipo").
COLS_DICIONARIO = {"subcategoria": "subcategoria", "categoria": "categoria", "tipo": "Tipo"}
COLS_MAPEAMENTO = {"padrao": "padrao", "subcategoria": "subcategoria"}

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
    sql = f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})"  # noqa: S608 -- nomes constantes, valores por ?
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
