"""Banco das tabelas editáveis (df-cns etapa 1, Task 1): categorias + mapeamento em SQLite."""
import sqlite3
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

from edicao import abrir, carga_inicial, ler_dicionario, ler_tabelas

DIC = pl.DataFrame({"subcategoria": ["Groceries", "Salario"],
                    "categoria": ["Alimentação", "Salário"],
                    "Tipo": ["Essencial", "Salário"]})
RULES = pl.DataFrame({"padrao": ["viacao ficticia"], "subcategoria": ["Onibus"]})


def test_abrir_cria_esquema(tmp_path):
    abrir(tmp_path / "e.db").close()
    conn = abrir(tmp_path / "e.db")
    dic = ler_dicionario(conn)
    assert dic.height == 0
    assert dic.columns == ["subcategoria", "categoria", "Tipo"]


def test_carga_inicial_insere(tmp_path):
    conn = abrir(tmp_path / "e.db")
    assert carga_inicial(conn, DIC, RULES) == (2, 1)
    dic, rules = ler_tabelas(conn)
    assert dic.sort("subcategoria").equals(DIC.sort("subcategoria"))
    assert rules.equals(RULES)


def test_carga_inicial_nao_sobrescreve(tmp_path):
    conn = abrir(tmp_path / "e.db")
    carga_inicial(conn, DIC, RULES)
    with conn:
        conn.execute("UPDATE categorias SET categoria = 'Editada' WHERE subcategoria = 'Groceries'")
    assert carga_inicial(conn, DIC, RULES) == (0, 0)
    dic = ler_dicionario(conn)
    assert dic.filter(pl.col("subcategoria") == "Groceries")["categoria"].item() == "Editada"


def test_ler_tabelas_falha_sem_carga(tmp_path):
    conn = abrir(tmp_path / "e.db")
    with pytest.raises(RuntimeError, match="carga_inicial_edicao"):
        ler_tabelas(conn)


def test_subcategoria_duplicada_rejeitada(tmp_path):
    conn = abrir(tmp_path / "e.db")
    dup = pl.concat([DIC, DIC.head(1)])
    with pytest.raises(sqlite3.IntegrityError):
        carga_inicial(conn, dup, RULES)
    assert ler_dicionario(conn).height == 0


def test_chaves_nao_aceitam_nulo(tmp_path):
    conn = abrir(tmp_path / "e.db")
    sem_sub = pl.DataFrame({"subcategoria": [None], "categoria": ["X"], "Tipo": ["Essencial"]},
                           schema={"subcategoria": pl.String, "categoria": pl.String, "Tipo": pl.String})
    with pytest.raises(sqlite3.IntegrityError):
        carga_inicial(conn, sem_sub, RULES)
    sem_padrao = pl.DataFrame({"padrao": [None], "subcategoria": ["Onibus"]},
                              schema={"padrao": pl.String, "subcategoria": pl.String})
    with pytest.raises(sqlite3.IntegrityError):
        carga_inicial(conn, DIC, sem_padrao)
