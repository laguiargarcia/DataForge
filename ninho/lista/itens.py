"""The rules of the list: validate and save its items in the table ``lista_itens`` of the app
database, created on first use. No FastAPI here: the routes only translate."""
import sqlite3
import uuid
from datetime import UTC, datetime

LIMITE = 200

_TABELA = """
CREATE TABLE IF NOT EXISTS lista_itens (
    id TEXT PRIMARY KEY,
    texto TEXT NOT NULL,
    comprado INTEGER NOT NULL DEFAULT 0,
    adicionado_por TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL,
    comprado_em TEXT
)"""
_COLUNAS = "id, texto, comprado, adicionado_por, criado_em, comprado_em"


class Invalido(ValueError):
    """The text of an item is empty or too long."""


class Ausente(LookupError):
    """No item has that id."""


def _agora() -> str:
    return datetime.now(UTC).isoformat()


def _tabela(conn: sqlite3.Connection) -> None:
    conn.execute(_TABELA)


def _item(linha: tuple) -> dict:
    id_, texto, comprado, adicionado_por, criado_em, comprado_em = linha
    return {"id": id_, "texto": texto, "comprado": bool(comprado), "adicionado_por": adicionado_por,
            "criado_em": criado_em, "comprado_em": comprado_em}


def _um(conn: sqlite3.Connection, id_: str) -> dict:
    linha = conn.execute(f"SELECT {_COLUNAS} FROM lista_itens WHERE id = ?", (id_,)).fetchone()  # noqa: S608 -- the columns are a constant
    if linha is None:
        raise Ausente(id_)
    return _item(linha)


def listar(conn: sqlite3.Connection) -> list[dict]:
    """The items still to buy, newest first, then the bought ones, most recently bought first."""
    _tabela(conn)
    linhas = conn.execute(
        f"SELECT {_COLUNAS} FROM lista_itens "  # noqa: S608 -- the columns are a constant
        "ORDER BY comprado, CASE WHEN comprado THEN comprado_em ELSE criado_em END DESC, rowid DESC"
    ).fetchall()
    return [_item(linha) for linha in linhas]


def adicionar(conn: sqlite3.Connection, texto: str, adicionado_por: str) -> dict:
    """A new item, not bought; the text loses the spaces at its ends."""
    texto = texto.strip()
    if not texto:
        raise Invalido("o item não pode ser vazio")
    if len(texto) > LIMITE:
        raise Invalido(f"o item passa de {LIMITE} caracteres")
    id_ = uuid.uuid4().hex
    with conn:
        _tabela(conn)
        conn.execute("INSERT INTO lista_itens (id, texto, adicionado_por, criado_em) VALUES (?, ?, ?, ?)",
                     (id_, texto, adicionado_por, _agora()))
    return _um(conn, id_)


def marcar(conn: sqlite3.Connection, id_: str, comprado: bool) -> dict:
    """Mark an item bought (with the time) or not bought."""
    with conn:
        _tabela(conn)
        cursor = conn.execute("UPDATE lista_itens SET comprado = ?, comprado_em = ? WHERE id = ?",
                              (int(comprado), _agora() if comprado else None, id_))
    if cursor.rowcount == 0:
        raise Ausente(id_)
    return _um(conn, id_)


def apagar(conn: sqlite3.Connection, id_: str) -> None:
    with conn:
        _tabela(conn)
        cursor = conn.execute("DELETE FROM lista_itens WHERE id = ?", (id_,))
    if cursor.rowcount == 0:
        raise Ausente(id_)


def limpar_comprados(conn: sqlite3.Connection) -> int:
    """Delete every bought item; answers how many went."""
    with conn:
        _tabela(conn)
        cursor = conn.execute("DELETE FROM lista_itens WHERE comprado = 1")
    return cursor.rowcount
