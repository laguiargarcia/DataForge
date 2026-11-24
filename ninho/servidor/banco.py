"""The app database of ninho (docs/architecture-v2/server-modules.md): one SQLite file named by
``NINHO_BANCO``, read on every request. Every module but finances keeps its tables here, each module
creating its own on first use. The server process writes to it, and so does the command
``python -m ninho pessoas`` (the table ``pessoas``), run on the same machine.

The folder of the file must exist: the server creates the file, never a folder, so a mistyped path is
an error and not a new database somewhere unexpected.
"""
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SemBanco(Exception):
    """``NINHO_BANCO`` is not set, or its folder does not exist."""


def caminho() -> Path:
    arquivo = os.environ.get("NINHO_BANCO")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    if not arquivo:
        raise SemBanco("NINHO_BANCO não definida")
    if not Path(arquivo).parent.is_dir():
        raise SemBanco("a pasta de NINHO_BANCO não existe")
    return Path(arquivo)


@contextmanager
def aberto(arquivo: Path) -> Iterator[sqlite3.Connection]:
    """A connection to the app database at <arquivo> (from ``caminho``), created when the file is
    missing. FastAPI may run a dependency and its route on different threads, so the connection is
    not tied to one."""
    conn = sqlite3.connect(arquivo, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
