"""Banco das tabelas editáveis (df-cns etapa 1, Task 1): categorias + mapeamento em SQLite; correções
e versão (df-cns.8.1)."""
import sqlite3
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

from edicao import abrir, carga_inicial, ler_correcoes, ler_dicionario, ler_tabelas, ler_versao

DIC = pl.DataFrame({"subcategoria": ["Groceries", "Salario"],
                    "categoria": ["Alimentação", "Salário"],
                    "Tipo": ["Essencial", "Salário"]})
RULES = pl.DataFrame({"padrao": ["viacao ficticia"], "subcategoria": ["Onibus"]})


@pytest.fixture
def aberto(tmp_path):
    """abrir() no e.db do teste; cada conexão que o teste abriu é fechada quando ele termina."""
    conexoes = []

    def _abrir():
        conexoes.append(abrir(tmp_path / "e.db"))
        return conexoes[-1]

    yield _abrir
    for conexao in conexoes:
        conexao.close()


def test_abrir_cria_esquema(aberto):
    aberto().close()
    conn = aberto()
    dic = ler_dicionario(conn)
    assert dic.height == 0
    assert dic.columns == ["subcategoria", "categoria", "Tipo"]


def test_carga_inicial_insere(aberto):
    conn = aberto()
    assert carga_inicial(conn, DIC, RULES) == (2, 1)
    dic, rules = ler_tabelas(conn)
    assert dic.sort("subcategoria").equals(DIC.sort("subcategoria"))
    assert rules.equals(RULES)


def test_carga_inicial_nao_sobrescreve(aberto):
    conn = aberto()
    carga_inicial(conn, DIC, RULES)
    with conn:
        conn.execute("UPDATE categorias SET categoria = 'Editada' WHERE subcategoria = 'Groceries'")
    assert carga_inicial(conn, DIC, RULES) == (0, 0)
    dic = ler_dicionario(conn)
    assert dic.filter(pl.col("subcategoria") == "Groceries")["categoria"].item() == "Editada"


def test_ler_tabelas_falha_sem_carga(aberto):
    conn = aberto()
    with pytest.raises(RuntimeError, match="carga_inicial_edicao"):
        ler_tabelas(conn)


def test_subcategoria_duplicada_rejeitada(aberto):
    conn = aberto()
    dup = pl.concat([DIC, DIC.head(1)])
    with pytest.raises(sqlite3.IntegrityError):
        carga_inicial(conn, dup, RULES)
    assert ler_dicionario(conn).height == 0


def test_chaves_nao_aceitam_nulo(aberto):
    conn = aberto()
    sem_sub = pl.DataFrame({"subcategoria": [None], "categoria": ["X"], "Tipo": ["Essencial"]},
                           schema={"subcategoria": pl.String, "categoria": pl.String, "Tipo": pl.String})
    with pytest.raises(sqlite3.IntegrityError):
        carga_inicial(conn, sem_sub, RULES)
    sem_padrao = pl.DataFrame({"padrao": [None], "subcategoria": ["Onibus"]},
                              schema={"padrao": pl.String, "subcategoria": pl.String})
    with pytest.raises(sqlite3.IntegrityError):
        carga_inicial(conn, DIC, sem_padrao)


def test_correcoes_e_versao_vazias_num_banco_novo(aberto):
    conn = aberto()
    assert ler_correcoes(conn).columns == ["transacao_id", "subcategoria"]
    assert ler_correcoes(conn).height == 0
    assert ler_versao(conn) == 0


def test_abrir_banco_antigo_cria_as_tabelas_novas(tmp_path, aberto):
    antigo = sqlite3.connect(tmp_path / "e.db")
    antigo.executescript("CREATE TABLE categorias (subcategoria TEXT PRIMARY KEY NOT NULL, categoria TEXT, tipo TEXT);"
                         "CREATE TABLE mapeamento (padrao TEXT PRIMARY KEY NOT NULL, subcategoria TEXT NOT NULL);"
                         "INSERT INTO categorias VALUES ('Groceries', 'Alimentação', 'Essencial');")
    antigo.commit()
    antigo.close()
    conn = aberto()
    assert ler_dicionario(conn).height == 1
    assert ler_correcoes(conn).height == 0
    assert ler_versao(conn) == 0


def test_ler_correcoes_e_versao(aberto):
    conn = aberto()
    with conn:
        conn.execute("INSERT INTO correcoes VALUES ('tx1', 'Groceries')")
        conn.execute("INSERT INTO versao (id, numero, editado_em) VALUES (1, 7, '2026-11-20T12:00:00Z')")
    assert ler_correcoes(conn).rows() == [("tx1", "Groceries")]
    assert ler_versao(conn) == 7


def test_versao_tem_uma_linha_so(aberto):
    conn = aberto()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO versao (id, numero) VALUES (2, 1)")


# --- divergências explicadas da conferência pelo saldo (df-y26v) ----------------------------------

def test_explicacao_grava_troca_e_desfaz_subindo_a_versao(aberto):
    from edicao import Recusa, excluir_explicacao, ler_explicacoes, salvar_explicacao
    conn = aberto()
    assert ler_explicacoes(conn).is_empty()
    salvar_explicacao(conn, "conta-ficticia", "2026-05-28T19:00:00+00:00", 9.93, "  seguro antes da coleta ")
    salvar_explicacao(conn, "conta-ficticia", "2026-05-28T19:00:00+00:00", 9.93, "seguro do cartão")
    assert ler_explicacoes(conn).rows() == [("conta-ficticia", "2026-05-28T19:00:00+00:00", 9.93, "seguro do cartão")]
    assert ler_versao(conn) == 2
    excluir_explicacao(conn, "conta-ficticia", "2026-05-28T19:00:00+00:00")
    assert ler_explicacoes(conn).is_empty()
    assert ler_versao(conn) == 3
    with pytest.raises(Recusa) as recusa:
        excluir_explicacao(conn, "conta-ficticia", "2026-05-28T19:00:00+00:00")
    assert recusa.value.motivo == "ausente"
    with pytest.raises(Recusa) as recusa:
        salvar_explicacao(conn, "conta-ficticia", "2026-05-28T19:00:00+00:00", 9.93, "   ")
    assert recusa.value.motivo == "invalido"
    assert ler_versao(conn) == 3   # recusa não muda nada
