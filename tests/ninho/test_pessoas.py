"""The people of ninho (df-8x0m.23.6.1): the table ``pessoas`` with hashed passwords, and the
command ``python -m ninho pessoas``. People and passwords are invented."""
import sqlite3
import subprocess
import sys

import pytest

from ninho import __main__ as comando
from ninho.servidor import banco, pessoas


@pytest.fixture
def arquivo(tmp_path):
    return tmp_path / "ninho.db"


@pytest.fixture
def conn(arquivo):
    with banco.aberto(arquivo) as c:
        yield c


def test_criar_guarda_a_pessoa_e_so_o_hash(conn):
    ana = pessoas.criar(conn, "  ana ", "segredo-1", ["lista"])
    assert ana["nome"] == "ana"
    assert ana["modulos"] == ["lista"]
    assert "senha_hash" not in ana
    guardado = conn.execute("SELECT senha_hash FROM pessoas").fetchone()[0]
    assert "segredo-1" not in guardado
    assert guardado.startswith("scrypt$")


def test_mesma_senha_da_hashes_diferentes(conn):
    pessoas.criar(conn, "ana", "igual", [])
    pessoas.criar(conn, "beto", "igual", [])
    um, outro = (h for (h,) in conn.execute("SELECT senha_hash FROM pessoas"))
    assert um != outro


@pytest.mark.parametrize("repetido", ["ana", "ANA", " Ana "])
def test_nome_repetido_e_recusado_sem_diferenciar_maiusculas(conn, repetido):
    pessoas.criar(conn, "ana", "s1", [])
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, repetido, "s2", [])
    assert len(pessoas.listar(conn)) == 1


def test_nome_repetido_com_acento_e_recusado(conn):
    pessoas.criar(conn, "élis", "s1", [])
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, "ÉLIS", "s2", [])


@pytest.mark.parametrize(("nome", "senha"), [("", "s"), ("   ", "s"), ("a:b", "s"),
                                             ("x" * (pessoas.LIMITE_NOME + 1), "s"), ("ana", "")])
def test_nome_ou_senha_invalidos_sao_recusados(conn, nome, senha):
    with pytest.raises(pessoas.Invalido):
        pessoas.criar(conn, nome, senha, [])
    assert pessoas.listar(conn) == []


def test_conferir_senha_certa_errada_e_pessoa_inexistente(conn):
    pessoas.criar(conn, "ana", "segredo-1", ["financas", "lista"])
    ana = pessoas.conferir(conn, "Ana", "segredo-1")
    assert ana is not None
    assert ana["nome"] == "ana"
    assert ana["modulos"] == ["financas", "lista"]
    assert pessoas.conferir(conn, "ana", "segredo-2") is None
    assert pessoas.conferir(conn, "ana", "") is None
    assert pessoas.conferir(conn, "beto", "segredo-1") is None


def test_conferir_pessoa_inexistente_tambem_calcula_um_hash(conn, monkeypatch):
    """Same time for an unknown name as for a wrong password: both go through one scrypt."""
    pessoas.criar(conn, "ana", "segredo-1", [])
    chamadas = []
    original = pessoas._derivar
    monkeypatch.setattr(pessoas, "_derivar", lambda *a: chamadas.append(a) or original(*a))
    pessoas._hash_falso()   # built once, outside the measure
    chamadas.clear()
    pessoas.conferir(conn, "beto", "x")
    pessoas.conferir(conn, "ana", "x")
    assert len(chamadas) == 2
    assert chamadas[0][2:] == chamadas[1][2:]   # same n, r, p


def test_conferir_num_banco_novo_nao_quebra(conn):
    assert pessoas.conferir(conn, "ana", "x") is None


def test_trocar_senha_a_antiga_deixa_de_valer(conn):
    pessoas.criar(conn, "ana", "antiga", ["lista"])
    pessoas.trocar_senha(conn, "ANA", "nova")
    assert pessoas.conferir(conn, "ana", "antiga") is None
    assert pessoas.conferir(conn, "ana", "nova")["modulos"] == ["lista"]


def test_trocar_senha_de_inexistente_ou_vazia(conn):
    with pytest.raises(pessoas.Ausente):
        pessoas.trocar_senha(conn, "beto", "nova")
    pessoas.criar(conn, "ana", "antiga", [])
    with pytest.raises(pessoas.Invalido):
        pessoas.trocar_senha(conn, "ana", "")
    assert pessoas.conferir(conn, "ana", "antiga") is not None


def test_definir_modulos_substitui_em_ordem_e_sem_repetir(conn):
    pessoas.criar(conn, "ana", "s", ["financas"])
    assert pessoas.definir_modulos(conn, "ana", ["lista", " financas", "lista", ""])["modulos"] == ["financas", "lista"]
    assert pessoas.definir_modulos(conn, "ana", [])["modulos"] == []
    assert pessoas.listar(conn)[0]["modulos"] == []


def test_modulo_desconhecido_e_recusado(conn):
    pessoas.criar(conn, "ana", "s", ["lista"])
    with pytest.raises(pessoas.Invalido, match="agenda"):
        pessoas.definir_modulos(conn, "ana", ["lista", "agenda"])
    with pytest.raises(pessoas.Invalido):
        pessoas.criar(conn, "beto", "s", ["agenda"])
    assert [p["nome"] for p in pessoas.listar(conn)] == ["ana"]
    assert pessoas.listar(conn)[0]["modulos"] == ["lista"]


def test_definir_modulos_de_inexistente(conn):
    with pytest.raises(pessoas.Ausente):
        pessoas.definir_modulos(conn, "beto", ["lista"])


def test_listar_em_ordem_de_criacao_sem_hash(conn):
    assert pessoas.listar(conn) == []
    pessoas.criar(conn, "beto", "s1", ["financas", "lista"])
    pessoas.criar(conn, "ana", "s2", ["lista"])
    todas = pessoas.listar(conn)
    assert [p["nome"] for p in todas] == ["beto", "ana"]
    assert all(set(p) == {"id", "nome", "modulos", "criada_em"} for p in todas)


def test_a_pessoa_sobrevive_a_reabrir_o_banco(conn, arquivo):
    pessoas.criar(conn, "ana", "segredo-1", ["lista"])
    with banco.aberto(arquivo) as outra:
        assert pessoas.conferir(outra, "ana", "segredo-1")["nome"] == "ana"


# The command, end to end: the password comes from the prompt (getpass), never from an argument.

@pytest.fixture
def prompt(monkeypatch):
    """What the person types at each getpass prompt, in order; records the prompts shown."""
    respostas: list[str] = []
    mostrados: list[str] = []

    def getpass(texto=""):
        mostrados.append(texto)
        return respostas.pop(0)

    monkeypatch.setattr(comando.getpass, "getpass", getpass)
    return respostas, mostrados


def _rodar(*args):
    return comando.main(["pessoas", *args])


def test_comando_criar_trocar_senha_modulos_e_listar(arquivo, prompt, capsys):
    respostas, mostrados = prompt
    respostas += ["segredo-1", "segredo-1"]
    assert _rodar("criar", "ana", "--modulos", "financas,lista", "--banco", str(arquivo)) == 0
    assert len(mostrados) == 2
    respostas += ["segredo-2", "segredo-2"]
    assert _rodar("senha", "ANA", "--banco", str(arquivo)) == 0
    assert _rodar("modulos", "ana", "lista", "--banco", str(arquivo)) == 0
    capsys.readouterr()
    assert _rodar("listar", "--banco", str(arquivo)) == 0
    saida = capsys.readouterr().out
    assert saida.startswith("ana\tlista\t")
    assert "segredo" not in saida
    assert "scrypt" not in saida
    with banco.aberto(arquivo) as conn:
        assert pessoas.conferir(conn, "ana", "segredo-1") is None
        assert pessoas.conferir(conn, "ana", "segredo-2")["modulos"] == ["lista"]


def test_comando_le_ninho_banco_do_ambiente(arquivo, prompt, monkeypatch, capsys):
    monkeypatch.setenv("NINHO_BANCO", str(arquivo))
    prompt[0].extend(["s", "s"])
    assert _rodar("criar", "beto") == 0
    capsys.readouterr()
    assert _rodar("listar") == 0
    assert capsys.readouterr().out.startswith("beto\t(nenhum módulo)\t")


def test_comando_senhas_diferentes_no_prompt_nao_cria(arquivo, prompt, capsys):
    prompt[0].extend(["uma", "outra"])
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert "não são iguais" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []


def test_comando_nome_repetido_falha_antes_de_pedir_a_senha(arquivo, prompt, capsys):
    with banco.aberto(arquivo) as conn:
        pessoas.criar(conn, "ana", "s", [])
    assert _rodar("criar", "Ana", "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert "já existe" in capsys.readouterr().err


def test_comando_senha_de_inexistente_falha_antes_de_pedir(arquivo, prompt, capsys):
    assert _rodar("senha", "beto", "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert "ninguém com o nome beto" in capsys.readouterr().err


def test_comando_modulo_desconhecido_falha(arquivo, prompt, capsys):
    assert _rodar("criar", "ana", "--modulos", "agenda", "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert "módulo desconhecido: agenda" in capsys.readouterr().err


def test_comando_sem_banco_falha_e_nao_cria_pasta(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NINHO_BANCO", raising=False)
    assert _rodar("listar") == 1
    assert "NINHO_BANCO" in capsys.readouterr().err
    assert _rodar("listar", "--banco", str(tmp_path / "nao-existe" / "ninho.db")) == 1
    assert not (tmp_path / "nao-existe").exists()


def test_comando_sem_acao_e_erro_de_uso():
    with pytest.raises(SystemExit) as saida:
        comando.main(["pessoas"])
    assert saida.value.code != 0


def test_python_m_ninho_roda_como_processo(arquivo):
    with banco.aberto(arquivo) as conn:
        pessoas.criar(conn, "ana", "s", ["lista"])
    r = subprocess.run([sys.executable, "-m", "ninho", "pessoas", "listar", "--banco", str(arquivo)],
                       capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ana\tlista\t")


def test_a_tabela_tem_nome_unico_no_banco(conn):
    """A second writer that skips the rules still cannot repeat a name in ASCII case."""
    pessoas.criar(conn, "ana", "s", [])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pessoas (id, nome, senha_hash, criada_em) VALUES ('x', 'ANA', 'h', 't')")


def test_comando_ctrl_c_no_prompt_nao_cria(arquivo, monkeypatch, capsys):
    def interromper(texto=""):
        raise KeyboardInterrupt
    monkeypatch.setattr(comando.getpass, "getpass", interromper)
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert "nada mudou" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []
