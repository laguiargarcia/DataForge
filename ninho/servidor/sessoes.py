"""The sessions of ninho (docs/architecture-v2/people-and-visibility.md, "The session"): the table
``sessoes`` of the app database, created on first use, and the rules on it. No FastAPI here.

A person who logged in holds a random token in the cookie ``COOKIE``; the table keeps only the
SHA-256 of it, so reading the database opens no session. A session lives ``VIDA`` from its last
use: using it pushes the expiry forward, but writes at most once per ``RENOVAR``, so a normal
request only reads. An expired session is deleted when it is asked for, and all of them when a
new one opens.
"""
import hashlib
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

COOKIE = "ninho_sessao"
VIDA = timedelta(days=90)   # from the last use (Lucas, 2026-11-24; df-8x0m.23.31)
RENOVAR = timedelta(days=1)

_TABELA = """
CREATE TABLE IF NOT EXISTS sessoes (
    token_hash TEXT PRIMARY KEY,
    pessoa_id TEXT NOT NULL,
    criada_em TEXT NOT NULL,
    usada_em TEXT NOT NULL,
    expira_em TEXT NOT NULL
)"""


def _tabela(conn: sqlite3.Connection) -> None:
    conn.execute(_TABELA)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _agora() -> datetime:
    return datetime.now(UTC)


def abrir(conn: sqlite3.Connection, pessoa_id: str) -> str:
    """A new session of the person; the token to hand to the browser, kept nowhere else."""
    token = secrets.token_urlsafe(32)
    agora = _agora()
    with conn:
        _tabela(conn)
        conn.execute("DELETE FROM sessoes WHERE expira_em <= ?", (agora.isoformat(),))
        conn.execute("INSERT INTO sessoes (token_hash, pessoa_id, criada_em, usada_em, expira_em) VALUES (?, ?, ?, ?, ?)",
                     (_hash(token), pessoa_id, agora.isoformat(), agora.isoformat(), (agora + VIDA).isoformat()))
    return token


def usar(conn: sqlite3.Connection, token: str) -> tuple[str, bool] | None:
    """The id of the person whose live session <token> is, and whether this use pushed its expiry
    forward (the cookie must then be sent again); None for an unknown or expired one."""
    _tabela(conn)
    chave, agora = _hash(token), _agora()
    linha = conn.execute("SELECT pessoa_id, usada_em, expira_em FROM sessoes WHERE token_hash = ?", (chave,)).fetchone()
    if linha is None:
        return None
    pessoa_id, usada_em, expira_em = linha
    if datetime.fromisoformat(expira_em) <= agora:
        with conn:
            conn.execute("DELETE FROM sessoes WHERE token_hash = ?", (chave,))
        return None
    if agora - datetime.fromisoformat(usada_em) < RENOVAR:
        return pessoa_id, False
    with conn:
        conn.execute("UPDATE sessoes SET usada_em = ?, expira_em = ? WHERE token_hash = ?",
                     (agora.isoformat(), (agora + VIDA).isoformat(), chave))
    return pessoa_id, True


def encerrar(conn: sqlite3.Connection, token: str) -> None:
    """End the session of <token>; nothing happens when there is none."""
    with conn:
        _tabela(conn)
        conn.execute("DELETE FROM sessoes WHERE token_hash = ?", (_hash(token),))


def encerrar_as_de(conn: sqlite3.Connection, pessoa_id: str) -> int:
    """End every session of the person, inside the caller's transaction; how many there were."""
    _tabela(conn)
    return conn.execute("DELETE FROM sessoes WHERE pessoa_id = ?", (pessoa_id,)).rowcount
