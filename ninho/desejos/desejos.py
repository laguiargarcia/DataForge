"""The rules of the wishlist: validate and save the wishes and their notes in the tables
``desejos_desejos`` and ``desejos_anotacoes`` of the app database, created on first use. No FastAPI
here: the routes only translate.

Whose a wish is (``de_quem``) is the id of a person, or None for both. The module never reads the
people itself: the caller hands it the person calling (``eu``, an id) and the people of the house
(``casa``, id -> name).

The surprise gift (df-8x0m.23.23): a person who is not the owner of a wish may mark "vou dar" on it,
a row of ``desejos_presentes``, at most one per wish. The owner must never find out, so every wish
leaves this module through ``_vista``, which gives the owner ``presente: None`` without reading the
mark, and the rules of the mark refuse the owner before they read it: whatever the owner asks, the
answer is the one it would be with no mark."""
import sqlite3
import unicodedata
import uuid
from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

LIMITE_TITULO = 200
LIMITE_ANOTACAO = 2000
LIMITE_ANOTACOES = 50   # notes per wish

_TABELAS = ("""
CREATE TABLE IF NOT EXISTS desejos_desejos (
    id TEXT PRIMARY KEY,
    titulo TEXT NOT NULL,
    de_quem TEXT,
    adicionado_por TEXT NOT NULL DEFAULT '',
    realizado INTEGER NOT NULL DEFAULT 0,
    realizado_em TEXT,
    criado_em TEXT NOT NULL
)""", """
CREATE TABLE IF NOT EXISTS desejos_anotacoes (
    id TEXT PRIMARY KEY,
    desejo_id TEXT NOT NULL,
    texto TEXT NOT NULL,
    criada_em TEXT NOT NULL
)""", "CREATE INDEX IF NOT EXISTS desejos_anotacoes_do_desejo ON desejos_anotacoes (desejo_id)", """
CREATE TABLE IF NOT EXISTS desejos_presentes (
    desejo_id TEXT PRIMARY KEY,
    quem TEXT NOT NULL,
    marcado_em TEXT NOT NULL
)""")
_COLUNAS = "id, titulo, de_quem, adicionado_por, realizado, realizado_em, criado_em"


class Invalido(ValueError):
    """A title, note or owner that cannot be kept."""


class Ausente(LookupError):
    """No wish (or no note of that wish) has that id."""


class DeOutraPessoa(ValueError):
    """The mark of the wish is someone else's: only who marked it unmarks it, and a wish has one."""


def _agora() -> str:
    return datetime.now(UTC).isoformat()


def _tabelas(conn: sqlite3.Connection) -> None:
    for comando in _TABELAS:
        conn.execute(comando)


def _proibido(caractere: str) -> bool:
    """Control characters (tab, line break, NUL...) and line or paragraph separators. Format,
    private and unassigned characters pass: an emoji joined by U+200D, a soft hyphen or an emoji
    newer than Python's table are what a phone keyboard types."""
    return unicodedata.category(caractere) in ("Cc", "Zl", "Zp")


def _texto(texto: object, limite: int, o_que: str) -> str:
    """<texto> without the spaces at its ends; Invalido when empty, longer than <limite> or with a
    control character."""
    if not isinstance(texto, str):
        raise Invalido(f"{o_que} tem de ser um texto")
    texto = texto.strip()
    if not texto:
        raise Invalido(f"{o_que} não pode ser vazio")
    if len(texto) > limite:
        raise Invalido(f"{o_que} passa de {limite} caracteres")
    if any(_proibido(c) for c in texto):
        raise Invalido(f"{o_que} não pode ter tab, quebra de linha nem outro caractere de controle")
    return texto


def _titulo(titulo: object) -> str:
    return _texto(titulo, LIMITE_TITULO, "o título")


def _anotacao(texto: object) -> str:
    return _texto(texto, LIMITE_ANOTACAO, "a anotação")


def _dono(de_quem: object, pessoas: Collection[str]) -> str | None:
    """The id of a person of the house, or None (empty also) for both; Invalido for anyone else."""
    if de_quem is None or (isinstance(de_quem, str) and not de_quem.strip()):
        return None
    if not isinstance(de_quem, str) or de_quem.strip() not in pessoas:
        raise Invalido("de_quem não é uma pessoa da casa")
    return de_quem.strip()


def e_link(texto: str) -> bool:
    """Whether the whole of <texto> is one http or https address (with a host, no spaces)."""
    if any(c.isspace() for c in texto):
        return False
    try:
        partes = urlsplit(texto)
    except ValueError:   # such as a malformed IPv6 host
        return False
    return partes.scheme in ("http", "https") and bool(partes.hostname)


def _anotacoes(conn: sqlite3.Connection, desejo_id: str) -> list[dict]:
    linhas = conn.execute("SELECT id, texto FROM desejos_anotacoes WHERE desejo_id = ? ORDER BY criada_em, rowid",
                          (desejo_id,)).fetchall()
    return [{"id": id_, "texto": texto, "link": e_link(texto)} for id_, texto in linhas]


def _presente(conn: sqlite3.Connection, desejo_id: str, eu: str, casa: Mapping[str, str]) -> dict | None:
    """The mark of a wish as a person who is not its owner sees it: who will give it and whether it
    is <eu>; None when nobody marked it."""
    marca = conn.execute("SELECT quem FROM desejos_presentes WHERE desejo_id = ?", (desejo_id,)).fetchone()
    if marca is None:
        return None
    return {"de": casa.get(marca[0], ""), "meu": marca[0] == eu}


def _vista(conn: sqlite3.Connection, linha: tuple, eu: str, casa: Mapping[str, str]) -> dict:
    """The one place a wish becomes what <eu> sees. The owner always gets ``presente: None`` and the
    mark is not even read for them; a wish of both has no mark."""
    id_, titulo, de_quem, adicionado_por, realizado, realizado_em, criado_em = linha
    secreto = de_quem is None or de_quem == eu
    return {"id": id_, "titulo": titulo, "de_quem": de_quem, "adicionado_por": adicionado_por,
            "realizado": bool(realizado), "realizado_em": realizado_em, "criado_em": criado_em,
            "anotacoes": _anotacoes(conn, id_),
            "presente": None if secreto else _presente(conn, id_, eu, casa)}


def _um(conn: sqlite3.Connection, id_: str, eu: str, casa: Mapping[str, str]) -> dict:
    linha = conn.execute(f"SELECT {_COLUNAS} FROM desejos_desejos WHERE id = ?", (id_,)).fetchone()  # noqa: S608 -- the columns are a constant
    if linha is None:
        raise Ausente(id_)
    return _vista(conn, linha, eu, casa)


def listar(conn: sqlite3.Connection, eu: str, casa: Mapping[str, str]) -> list[dict]:
    """Every wish as <eu> sees it: the ones not fulfilled first, newest on top, then the fulfilled
    ones, most recently fulfilled on top; each with its notes in the order they were written. The
    order never looks at the marks."""
    _tabelas(conn)
    linhas = conn.execute(
        f"SELECT {_COLUNAS} FROM desejos_desejos "  # noqa: S608 -- the columns are a constant
        "ORDER BY realizado, CASE WHEN realizado THEN realizado_em ELSE criado_em END DESC, rowid DESC"
    ).fetchall()
    return [_vista(conn, linha, eu, casa) for linha in linhas]


def _inserir_anotacoes(conn: sqlite3.Connection, desejo_id: str, textos: Iterable[str]) -> None:
    for texto in textos:
        conn.execute("INSERT INTO desejos_anotacoes (id, desejo_id, texto, criada_em) VALUES (?, ?, ?, ?)",
                     (uuid.uuid4().hex, desejo_id, texto, _agora()))


def adicionar(conn: sqlite3.Connection, pedido: Mapping[str, object], eu: str, casa: Mapping[str, str]) -> dict:
    """A new wish, not fulfilled, added by <eu>, from <pedido>: ``titulo``, ``de_quem`` (one of the
    ids of <casa>, or None for both) and, optionally, ``anotacoes`` (texts, kept in that order).
    Titles and notes lose the spaces at their ends."""
    titulo, dono = _titulo(pedido.get("titulo")), _dono(pedido.get("de_quem"), casa)
    anotacoes = pedido.get("anotacoes") or []
    if not isinstance(anotacoes, list):
        raise Invalido("anotacoes tem de ser uma lista de textos")
    if len(anotacoes) > LIMITE_ANOTACOES:
        raise Invalido(f"um desejo tem no máximo {LIMITE_ANOTACOES} anotações")
    textos = [_anotacao(texto) for texto in anotacoes]
    id_ = uuid.uuid4().hex
    with conn:
        _tabelas(conn)
        conn.execute("INSERT INTO desejos_desejos (id, titulo, de_quem, adicionado_por, criado_em) VALUES (?, ?, ?, ?, ?)",
                     (id_, titulo, dono, casa.get(eu, ""), _agora()))
        _inserir_anotacoes(conn, id_, textos)
    return _um(conn, id_, eu, casa)


def editar(conn: sqlite3.Connection, id_: str, mudancas: Mapping[str, object], eu: str,
           casa: Mapping[str, str]) -> dict:
    """Change what <mudancas> gives of a wish: ``titulo``, ``de_quem`` (moving it to another part)
    or ``realizado``. Fulfilling keeps the time (the first one, when it already was); undoing clears it.
    Moving it to another owner deletes its mark: the one who would give it may be the new owner."""
    desconhecidos = sorted(set(mudancas) - {"titulo", "de_quem", "realizado"})
    if desconhecidos:
        raise Invalido(f"campo desconhecido: {', '.join(desconhecidos)}")
    validos: dict[str, object] = {}
    if "titulo" in mudancas:
        validos["titulo"] = _titulo(mudancas["titulo"])
    if "de_quem" in mudancas:
        validos["de_quem"] = _dono(mudancas["de_quem"], casa)
    if "realizado" in mudancas:
        if not isinstance(mudancas["realizado"], bool):
            raise Invalido("realizado tem de ser verdadeiro ou falso")
        validos["realizado"] = int(mudancas["realizado"])
    with conn:
        _tabelas(conn)
        if conn.execute("SELECT 1 FROM desejos_desejos WHERE id = ?", (id_,)).fetchone() is None:
            raise Ausente(id_)
        if "de_quem" in validos:
            conn.execute("DELETE FROM desejos_presentes WHERE desejo_id = ? AND EXISTS "
                         "(SELECT 1 FROM desejos_desejos WHERE id = ? AND de_quem IS NOT ?)",
                         (id_, id_, validos["de_quem"]))
        for coluna, valor in validos.items():   # the column names come from the keys above, never from outside
            conn.execute(f"UPDATE desejos_desejos SET {coluna} = ? WHERE id = ?", (valor, id_))  # noqa: S608 -- a fixed column name
        if "realizado" in validos:
            conn.execute("UPDATE desejos_desejos SET realizado_em = CASE WHEN realizado "
                         "THEN COALESCE(realizado_em, ?) END WHERE id = ?", (_agora(), id_))
    return _um(conn, id_, eu, casa)


def apagar(conn: sqlite3.Connection, id_: str) -> None:
    """Delete a wish, its notes and its mark."""
    with conn:
        _tabelas(conn)
        cursor = conn.execute("DELETE FROM desejos_desejos WHERE id = ?", (id_,))
        conn.execute("DELETE FROM desejos_anotacoes WHERE desejo_id = ?", (id_,))
        conn.execute("DELETE FROM desejos_presentes WHERE desejo_id = ?", (id_,))
    if cursor.rowcount == 0:
        raise Ausente(id_)


def anotar(conn: sqlite3.Connection, id_: str, texto: str, eu: str, casa: Mapping[str, str]) -> dict:
    """Add a note at the end of a wish; answers the wish. The count and the insert are one write
    transaction, so two notes at once cannot pass the limit together."""
    texto = _anotacao(texto)
    _tabelas(conn)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM desejos_desejos WHERE id = ?", (id_,)).fetchone() is None:
            raise Ausente(id_)
        (quantas,) = conn.execute("SELECT COUNT(*) FROM desejos_anotacoes WHERE desejo_id = ?", (id_,)).fetchone()
        if quantas >= LIMITE_ANOTACOES:
            raise Invalido(f"um desejo tem no máximo {LIMITE_ANOTACOES} anotações")
        _inserir_anotacoes(conn, id_, [texto])
    return _um(conn, id_, eu, casa)


def desanotar(conn: sqlite3.Connection, id_: str, anotacao_id: str) -> None:
    """Delete one note of a wish; Ausente when that wish has no such note."""
    with conn:
        _tabelas(conn)
        cursor = conn.execute("DELETE FROM desejos_anotacoes WHERE id = ? AND desejo_id = ?", (anotacao_id, id_))
    if cursor.rowcount == 0:
        raise Ausente(anotacao_id)


def _marcavel(conn: sqlite3.Connection, id_: str, eu: str) -> None:
    """Ausente when there is no such wish; Invalido when it is <eu>'s own or both's. Asked before the
    mark is read, so what the owner gets never depends on it."""
    linha = conn.execute("SELECT de_quem FROM desejos_desejos WHERE id = ?", (id_,)).fetchone()
    if linha is None:
        raise Ausente(id_)
    if linha[0] is None:
        raise Invalido("um desejo dos dois não tem presente-surpresa")
    if linha[0] == eu:
        raise Invalido("o desejo é seu: o presente-surpresa é de quem vai dar")


def marcar(conn: sqlite3.Connection, id_: str, eu: str, casa: Mapping[str, str]) -> dict:
    """<eu> will give the wish of another person; answers the wish. Marking again is no change;
    DeOutraPessoa when someone else already marked it."""
    _tabelas(conn)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _marcavel(conn, id_, eu)
        marca = conn.execute("SELECT quem FROM desejos_presentes WHERE desejo_id = ?", (id_,)).fetchone()
        if marca is None:
            conn.execute("INSERT INTO desejos_presentes (desejo_id, quem, marcado_em) VALUES (?, ?, ?)",
                         (id_, eu, _agora()))
        elif marca[0] != eu:
            raise DeOutraPessoa("outra pessoa já vai dar este desejo")
    return _um(conn, id_, eu, casa)


def desmarcar(conn: sqlite3.Connection, id_: str, eu: str) -> None:
    """<eu> will not give it after all. Unmarking a wish with no mark is no change; DeOutraPessoa
    when the mark is someone else's."""
    _tabelas(conn)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _marcavel(conn, id_, eu)
        cursor = conn.execute("DELETE FROM desejos_presentes WHERE desejo_id = ? AND quem = ?", (id_, eu))
        if cursor.rowcount == 0 and conn.execute("SELECT 1 FROM desejos_presentes WHERE desejo_id = ?",
                                                 (id_,)).fetchone() is not None:
            raise DeOutraPessoa("só quem marcou desmarca")
