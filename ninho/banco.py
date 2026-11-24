"""The app database: everything a person typed. Only the house server writes to it (ADR 0006,
docs/architecture-v2/edits.md).

For now it holds the finance edits and is the SQLite file the workspace's pipeline reads as its
``edicao`` connection, so the pipeline sees an edit without a copy. The server creates the file and
its tables on first use; the pipeline opens it read-only.
"""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ninho.financas import regras


def caminho(workspace: Path) -> Path:
    return workspace / "data" / "edicao.db"


@contextmanager
def aberto(workspace: Path) -> Iterator[sqlite3.Connection]:
    """A connection of the server to the app database, created with its tables when missing."""
    conn = regras.edicao(workspace).abrir(caminho(workspace))
    try:
        yield conn
    finally:
        conn.close()
