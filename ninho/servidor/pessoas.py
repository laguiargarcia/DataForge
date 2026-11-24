"""The people who may log in to ninho (docs/architecture-v2/people-and-visibility.md): the table
``pessoas`` of the app database, created on first use, and the rules on it. No FastAPI here.

A password is kept only as a scrypt hash (standard library) with a salt of its own, in the form
``scrypt$<n>$<r>$<p>$<salt hex>$<key hex>``: the parameters travel with the hash, so raising them
later leaves the old hashes valid, and ``conferir`` says when one is below the current cost so the
login can write it again. A name is unique without telling upper from lower case; names and
passwords are compared in Unicode NFC, so the same text typed on two keyboards is the same.
"""
import hashlib
import secrets
import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime

MODULOS = ("financas", "lista")
LIMITE_NOME = 50
SENHA_MINIMA = 8   # characters; proposed by Claude, not yet confirmed by Lucas (df-8x0m.23.6.5)

# OWASP's scrypt minimum, the variant with less memory: n=2^15, r=8, p=3 costs as much CPU as
# n=2^17 with p=1 but needs 32 MiB instead of 128 MiB per check, and runs on prod's Windows Python.
_N, _R, _P = 2**15, 8, 3
_MEMORIA = 64 * 1024 * 1024   # scrypt needs 128 * r * n bytes (32 MiB here); OpenSSL's default cap is 32 MiB
_TAMANHO = 32

# Checked against when the name is unknown, so that costs one scrypt like a wrong password. A random
# key no password derives to: building it costs nothing.
_SAL_FALSO, _CHAVE_FALSA = secrets.token_hex(16), secrets.token_hex(_TAMANHO)

_TABELA = """
CREATE TABLE IF NOT EXISTS pessoas (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
    senha_hash TEXT NOT NULL,
    modulos TEXT NOT NULL DEFAULT '',
    criada_em TEXT NOT NULL
)"""


class Invalido(ValueError):
    """A name, password or module that cannot be kept."""


class Repetido(ValueError):
    """Someone already has that name, in any case."""


class Ausente(LookupError):
    """Nobody has that name."""


def _tabela(conn: sqlite3.Connection) -> None:
    conn.execute(_TABELA)


def _nfc(texto: str) -> str:
    return unicodedata.normalize("NFC", texto)


def _derivar(senha: str, sal: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(_nfc(senha).encode(), salt=sal, n=n, r=r, p=p, maxmem=_MEMORIA, dklen=_TAMANHO)


def gerar_hash(senha: str) -> str:
    sal = secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${sal.hex()}${_derivar(senha, sal, _N, _R, _P).hex()}"


def _partes(guardado: str) -> tuple[int, int, int, bytes, bytes]:
    """(n, r, p, salt, key) of a stored hash; ValueError when it is not one."""
    tipo, n, r, p, sal, chave = guardado.split("$")
    if tipo != "scrypt":
        raise ValueError("não é um hash scrypt")
    return int(n), int(r), int(p), bytes.fromhex(sal), bytes.fromhex(chave)


def _confere(senha: str, guardado: str) -> bool:
    """Whether <senha> derives to <guardado>; False, never an exception, for a malformed hash."""
    try:
        n, r, p, sal, chave = _partes(guardado)
        return secrets.compare_digest(_derivar(senha, sal, n, r, p), chave)
    except (ValueError, OverflowError):   # a broken row, bad parameters or over the memory cap
        return False


def precisa_refazer(guardado: str) -> bool:
    """Whether <guardado> was made with other parameters than today's, so it should be made again."""
    try:
        return _partes(guardado)[:3] != (_N, _R, _P)
    except ValueError:
        return True


def _hash_falso() -> str:
    return f"scrypt${_N}${_R}${_P}${_SAL_FALSO}${_CHAVE_FALSA}"


def _chave(nome: str) -> str:
    """What two names are compared by: canonical caseless (NFD of the casefold of the NFD)."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", nome.strip()).casefold())


def _proibido(caractere: str) -> bool:
    """Control, format, private or unassigned characters and line or paragraph separators."""
    return unicodedata.category(caractere)[0] == "C" or unicodedata.category(caractere) in ("Zl", "Zp")


def validar_nome(nome: str) -> str:
    """<nome> as it is kept (NFC, no spaces at its ends); Invalido when it cannot be one."""
    nome = _nfc(nome.strip())
    if not nome:
        raise Invalido("o nome não pode ser vazio")
    if len(nome) > LIMITE_NOME:
        raise Invalido(f"o nome passa de {LIMITE_NOME} caracteres")
    if ":" in nome:
        raise Invalido("o nome não pode ter ':' (o login do navegador separa nome e senha por ele)")
    if any(_proibido(c) for c in nome):
        raise Invalido("o nome não pode ter tab, quebra de linha nem outro caractere de controle")
    return nome


def validar_senha(senha: str) -> str:
    if len(_nfc(senha)) < SENHA_MINIMA:
        raise Invalido(f"a senha precisa de pelo menos {SENHA_MINIMA} caracteres")
    return senha


def modulos_validos(modulos: list[str]) -> list[str]:
    """<modulos> without blanks or repeats, in the order of ``MODULOS``; Invalido for an unknown one."""
    pedidos = {m.strip() for m in modulos if m.strip()}
    desconhecidos = sorted(pedidos - set(MODULOS))
    if desconhecidos:
        raise Invalido(f"módulo desconhecido: {', '.join(desconhecidos)} (os que existem: {', '.join(MODULOS)})")
    return [m for m in MODULOS if m in pedidos]


def _pessoa(linha: tuple) -> dict:
    id_, nome, modulos, criada_em = linha
    return {"id": id_, "nome": nome, "modulos": modulos.split(",") if modulos else [], "criada_em": criada_em}


def _achar(conn: sqlite3.Connection, nome: str) -> tuple | None:
    """The row (id, nome, modulos, criada_em, senha_hash) of <nome>, any case, or None. Compared in
    Python: SQLite's NOCASE folds only ASCII and knows nothing of NFC."""
    chave = _chave(nome)
    for linha in conn.execute("SELECT id, nome, modulos, criada_em, senha_hash FROM pessoas"):
        if _chave(linha[1]) == chave:
            return linha
    return None


def existe(conn: sqlite3.Connection, nome: str) -> bool:
    _tabela(conn)
    return _achar(conn, nome) is not None


def _existente(conn: sqlite3.Connection, nome: str) -> tuple:
    linha = _achar(conn, nome)
    if linha is None:
        raise Ausente(_nfc(nome.strip()))
    return linha


def criar(conn: sqlite3.Connection, nome: str, senha: str, modulos: list[str]) -> dict:
    """A new person; the name is kept in NFC without the spaces at its ends. The check for the name
    and the insert are one write transaction, so two commands at once cannot both create it."""
    nome, lista = validar_nome(nome), ",".join(modulos_validos(modulos))
    senha_hash = gerar_hash(validar_senha(senha))   # before the transaction: no lock held while hashing
    _tabela(conn)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if _achar(conn, nome) is not None:
                raise Repetido(f"já existe alguém com o nome {nome}")
            conn.execute("INSERT INTO pessoas (id, nome, senha_hash, modulos, criada_em) VALUES (?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex, nome, senha_hash, lista, datetime.now(UTC).isoformat()))
    except sqlite3.IntegrityError as erro:
        raise Repetido(f"já existe alguém com o nome {nome}") from erro
    return _pessoa(_existente(conn, nome)[:4])


def trocar_senha(conn: sqlite3.Connection, nome: str, senha: str) -> None:
    senha_hash = gerar_hash(validar_senha(senha))
    with conn:
        _tabela(conn)
        id_ = _existente(conn, nome)[0]
        conn.execute("UPDATE pessoas SET senha_hash = ? WHERE id = ?", (senha_hash, id_))


def definir_modulos(conn: sqlite3.Connection, nome: str, modulos: list[str]) -> dict:
    """Replace the modules of <nome>; an empty list leaves the person with none."""
    lista = ",".join(modulos_validos(modulos))
    with conn:
        _tabela(conn)
        id_ = _existente(conn, nome)[0]
        conn.execute("UPDATE pessoas SET modulos = ? WHERE id = ?", (lista, id_))
    return _pessoa(_existente(conn, nome)[:4])


def conferir(conn: sqlite3.Connection, nome: str, senha: str) -> dict | None:
    """The person when <senha> is the password of <nome>, else None (also for a malformed stored
    hash). An unknown name still costs one hash check, the same time as a wrong password. The person
    carries ``precisa_refazer``: True when the stored hash is not at today's cost, so the caller may
    keep a new one with ``trocar_senha``."""
    _tabela(conn)
    linha = _achar(conn, nome)
    certa = _confere(senha, linha[4] if linha else _hash_falso())
    if not (linha and certa):
        return None
    return _pessoa(linha[:4]) | {"precisa_refazer": precisa_refazer(linha[4])}


def listar(conn: sqlite3.Connection) -> list[dict]:
    """Everyone, in the order they were created, without the hash."""
    _tabela(conn)
    return [_pessoa(linha) for linha in
            conn.execute("SELECT id, nome, modulos, criada_em FROM pessoas ORDER BY criada_em, rowid")]
