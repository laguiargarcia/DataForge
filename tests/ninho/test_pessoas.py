"""The people of ninho (df-8x0m.23.6.1, df-8x0m.23.6.5): the table ``pessoas`` with hashed
passwords, and the command ``python -m ninho pessoas``. People and passwords are invented."""
import sqlite3
import subprocess
import sys
import unicodedata

import pytest

from ninho import __main__ as comando
from ninho.servidor import banco, pessoas

CUSTO_REAL = (pessoas._N, pessoas._R, pessoas._P)
S1, S2 = "segredo-um", "segredo-dois"


@pytest.fixture
def custo_real(monkeypatch):
    """The real scrypt cost, over the cheap one of conftest's ``custo_baixo``."""
    for nome, valor in zip(("_N", "_R", "_P"), CUSTO_REAL, strict=True):
        monkeypatch.setattr(pessoas, nome, valor)


@pytest.fixture
def arquivo(tmp_path):
    return tmp_path / "ninho.db"


@pytest.fixture
def conn(arquivo):
    with banco.aberto(arquivo) as c:
        yield c


def _hash_de(conn, nome="ana"):
    return conn.execute("SELECT senha_hash FROM pessoas WHERE nome = ?", (nome,)).fetchone()[0]


def test_criar_guarda_a_pessoa_e_so_o_hash(conn):
    ana = pessoas.criar(conn, "  ana ", S1, ["lista"])
    assert ana["nome"] == "ana"
    assert ana["modulos"] == ["lista"]
    assert "senha_hash" not in ana
    guardado = _hash_de(conn)
    assert S1 not in guardado
    assert guardado.startswith("scrypt$")


def test_custo_real_e_o_minimo_da_owasp(conn, custo_real):
    """n=2^15, r=8, p=3 (OWASP's lower-memory minimum) runs with the memory cap set."""
    assert CUSTO_REAL == (2**15, 8, 3)
    pessoas.criar(conn, "ana", S1, [])
    assert _hash_de(conn).startswith("scrypt$32768$8$3$")
    ana = pessoas.conferir(conn, "ana", S1)
    assert ana is not None
    assert ana["precisa_refazer"] is False


def test_mesma_senha_da_hashes_diferentes(conn):
    pessoas.criar(conn, "ana", S1, [])
    pessoas.criar(conn, "beto", S1, [])
    um, outro = (h for (h,) in conn.execute("SELECT senha_hash FROM pessoas"))
    assert um != outro


@pytest.mark.parametrize("repetido", ["ana", "ANA", " Ana "])
def test_nome_repetido_e_recusado_sem_diferenciar_maiusculas(conn, repetido):
    pessoas.criar(conn, "ana", S1, [])
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, repetido, S2, [])
    assert len(pessoas.listar(conn)) == 1


def test_nome_repetido_com_acento_e_recusado(conn):
    pessoas.criar(conn, "élis", S1, [])
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, "ÉLIS", S2, [])


def test_mesmo_nome_em_nfc_e_nfd_e_uma_pessoa_so(conn):
    nfc, nfd = unicodedata.normalize("NFC", "élis"), unicodedata.normalize("NFD", "élis")
    assert nfc != nfd
    assert pessoas.criar(conn, nfd, S1, [])["nome"] == nfc
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, nfc, S2, [])
    assert pessoas.conferir(conn, nfc, S1)["nome"] == nfc
    assert pessoas.conferir(conn, nfd, S1)["nome"] == nfc


def test_senha_em_nfc_e_nfd_e_a_mesma(conn):
    senha = "pão-de-açúcar"
    pessoas.criar(conn, "ana", unicodedata.normalize("NFD", senha), [])
    assert pessoas.conferir(conn, "ana", unicodedata.normalize("NFC", senha)) is not None


@pytest.mark.parametrize(("nome", "senha"), [("", S1), ("   ", S1), ("a:b", S1),
                                             ("x" * (pessoas.LIMITE_NOME + 1), S1), ("ana", ""),
                                             ("a\tb", S1), ("a\nb", S1), ("a\x00b", S1), ("a\x1bb", S1),
                                             ("a\u2028b", S1), ("a\u200bb", S1)])
def test_nome_ou_senha_invalidos_sao_recusados(conn, nome, senha):
    with pytest.raises(pessoas.Invalido):
        pessoas.criar(conn, nome, senha, [])
    assert pessoas.listar(conn) == []


def test_senha_com_menos_do_minimo_e_recusada(conn):
    curta = "x" * (pessoas.SENHA_MINIMA - 1)
    with pytest.raises(pessoas.Invalido, match=str(pessoas.SENHA_MINIMA)):
        pessoas.criar(conn, "ana", curta, [])
    pessoas.criar(conn, "ana", curta + "x", [])
    with pytest.raises(pessoas.Invalido):
        pessoas.trocar_senha(conn, "ana", curta)
    assert pessoas.conferir(conn, "ana", curta + "x") is not None


def test_conferir_senha_certa_errada_e_pessoa_inexistente(conn):
    pessoas.criar(conn, "ana", S1, ["financas", "lista"])
    ana = pessoas.conferir(conn, "Ana", S1)
    assert ana is not None
    assert ana["nome"] == "ana"
    assert ana["modulos"] == ["financas", "lista"]
    assert pessoas.conferir(conn, "ana", S2) is None
    assert pessoas.conferir(conn, "ana", "") is None
    assert pessoas.conferir(conn, "beto", S1) is None


def test_conferir_pessoa_inexistente_tambem_calcula_um_hash(conn, monkeypatch):
    """Same time for an unknown name as for a wrong password: both go through one scrypt, the
    first unknown name of the process included."""
    pessoas.criar(conn, "ana", S1, [])
    chamadas = []
    original = pessoas._derivar
    monkeypatch.setattr(pessoas, "_derivar", lambda *a: chamadas.append(a) or original(*a))
    pessoas.conferir(conn, "beto", "x")
    pessoas.conferir(conn, "ana", "x")
    assert len(chamadas) == 2
    assert chamadas[0][2:] == chamadas[1][2:]   # same n, r, p


@pytest.mark.parametrize("guardado", ["lixo", "", "scrypt$1$2$3", "bcrypt$16$8$1$00$00",
                                      "scrypt$x$8$1$00$00", "scrypt$16$8$1$zz$00", "scrypt$17$8$1$00$00",
                                      "scrypt$1073741824$8$1$00$00", f"scrypt${2**80}$8$1$00$00",
                                      "scrypt$16$8$1$00$00$00"])
def test_hash_guardado_malformado_e_senha_errada(conn, guardado):
    pessoas.criar(conn, "ana", S1, [])
    conn.execute("UPDATE pessoas SET senha_hash = ?", (guardado,))
    assert pessoas.conferir(conn, "ana", S1) is None
    assert pessoas.precisa_refazer(guardado)


def test_hash_abaixo_do_custo_atual_precisa_refazer(conn, monkeypatch):
    pessoas.criar(conn, "ana", S1, [])
    assert pessoas.conferir(conn, "ana", S1)["precisa_refazer"] is False
    monkeypatch.setattr(pessoas, "_N", 2**5)
    ana = pessoas.conferir(conn, "ana", S1)
    assert ana["precisa_refazer"] is True
    pessoas.trocar_senha(conn, "ana", S1)
    assert pessoas.conferir(conn, "ana", S1)["precisa_refazer"] is False
    assert _hash_de(conn).startswith("scrypt$32$")


def test_conferir_num_banco_novo_nao_quebra(conn):
    assert pessoas.conferir(conn, "ana", "x") is None


def test_trocar_senha_a_antiga_deixa_de_valer(conn):
    pessoas.criar(conn, "ana", S1, ["lista"])
    pessoas.trocar_senha(conn, "ANA", S2)
    assert pessoas.conferir(conn, "ana", S1) is None
    assert pessoas.conferir(conn, "ana", S2)["modulos"] == ["lista"]


def test_trocar_senha_de_inexistente_ou_vazia(conn):
    with pytest.raises(pessoas.Ausente):
        pessoas.trocar_senha(conn, "beto", S2)
    pessoas.criar(conn, "ana", S1, [])
    with pytest.raises(pessoas.Invalido):
        pessoas.trocar_senha(conn, "ana", "")
    assert pessoas.conferir(conn, "ana", S1) is not None


def test_definir_modulos_substitui_em_ordem_e_sem_repetir(conn):
    pessoas.criar(conn, "ana", S1, ["financas"])
    assert pessoas.definir_modulos(conn, "ana", ["lista", " financas", "lista", ""])["modulos"] == ["financas", "lista"]
    assert pessoas.definir_modulos(conn, "ana", [])["modulos"] == []
    assert pessoas.listar(conn)[0]["modulos"] == []


def test_modulo_desconhecido_e_recusado(conn):
    pessoas.criar(conn, "ana", S1, ["lista"])
    with pytest.raises(pessoas.Invalido, match="agenda"):
        pessoas.definir_modulos(conn, "ana", ["lista", "agenda"])
    with pytest.raises(pessoas.Invalido):
        pessoas.criar(conn, "beto", S1, ["agenda"])
    assert [p["nome"] for p in pessoas.listar(conn)] == ["ana"]
    assert pessoas.listar(conn)[0]["modulos"] == ["lista"]


def test_definir_modulos_de_inexistente(conn):
    with pytest.raises(pessoas.Ausente):
        pessoas.definir_modulos(conn, "beto", ["lista"])


def test_listar_em_ordem_de_criacao_sem_hash(conn):
    assert pessoas.listar(conn) == []
    pessoas.criar(conn, "beto", S1, ["financas", "lista"])
    pessoas.criar(conn, "ana", S2, ["lista"])
    todas = pessoas.listar(conn)
    assert [p["nome"] for p in todas] == ["beto", "ana"]
    assert all(set(p) == {"id", "nome", "modulos", "criada_em"} for p in todas)


def test_a_pessoa_sobrevive_a_reabrir_o_banco(conn, arquivo):
    pessoas.criar(conn, "ana", S1, ["lista"])
    with banco.aberto(arquivo) as outra:
        assert pessoas.conferir(outra, "ana", S1)["nome"] == "ana"


def test_a_tabela_tem_nome_unico_no_banco(conn):
    """A second writer that skips the rules still cannot repeat a name in ASCII case."""
    pessoas.criar(conn, "ana", S1, [])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pessoas (id, nome, senha_hash, criada_em) VALUES ('x', 'ANA', 'h', 't')")


def test_criar_confere_o_nome_dentro_da_transacao_de_escrita(conn, arquivo, monkeypatch):
    """The name is checked after BEGIN IMMEDIATE: another writer that slips in between the caller's
    own check and the insert is seen, never a second row."""
    pessoas.listar(conn)   # the table exists
    original = pessoas._achar
    vistos = []

    def achar(c, nome):
        vistos.append(c.in_transaction)
        return original(c, nome)

    monkeypatch.setattr(pessoas, "_achar", achar)
    pessoas.criar(conn, "ana", S1, [])
    assert vistos[0] is True
    with banco.aberto(arquivo) as outro:
        outro.execute("INSERT INTO pessoas (id, nome, senha_hash, criada_em) VALUES ('x', 'Élis', 'h', 't')")
        outro.commit()
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, "élis", S1, [])
    assert len(pessoas.listar(conn)) == 2


def test_integrity_error_no_insert_vira_repetido(conn, monkeypatch):
    """When the check misses a name the UNIQUE column catches (ASCII case), the error is Repetido."""
    pessoas.criar(conn, "ana", S1, [])
    monkeypatch.setattr(pessoas, "_achar", lambda c, nome: None)
    with pytest.raises(pessoas.Repetido):
        pessoas.criar(conn, "ANA", S2, [])
    assert not conn.in_transaction


# The command, end to end: the password comes from the prompt (getpass), never from an argument.

@pytest.fixture
def prompt(monkeypatch):
    """What the person types at each getpass prompt, in order; records the prompts shown. The input
    counts as a terminal."""
    respostas: list[str] = []
    mostrados: list[str] = []

    def getpass(texto=""):
        mostrados.append(texto)
        return respostas.pop(0)

    monkeypatch.setattr(comando.getpass, "getpass", getpass)
    monkeypatch.setattr(comando, "_terminal", lambda: True)
    return respostas, mostrados


def _rodar(*args):
    return comando.main(["pessoas", *args])


def test_comando_criar_trocar_senha_modulos_e_listar(arquivo, prompt, capsys):
    respostas, mostrados = prompt
    respostas += [S1, S1]
    assert _rodar("criar", "ana", "--modulos", "financas,lista", "--banco", str(arquivo)) == 0
    assert len(mostrados) == 2
    respostas += [S2, S2]
    assert _rodar("senha", "ANA", "--banco", str(arquivo)) == 0
    assert _rodar("modulos", "ana", "lista", "--banco", str(arquivo)) == 0
    capsys.readouterr()
    assert _rodar("listar", "--banco", str(arquivo)) == 0
    saida = capsys.readouterr().out
    assert saida.startswith("ana\tlista\t")
    assert "segredo" not in saida
    assert "scrypt" not in saida
    with banco.aberto(arquivo) as conn:
        assert pessoas.conferir(conn, "ana", S1) is None
        assert pessoas.conferir(conn, "ana", S2)["modulos"] == ["lista"]


def test_comando_criar_e_senha_nao_mostram_a_senha(arquivo, prompt, capsys):
    prompt[0].extend([S1, S1, S2, S2])
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 0
    assert _rodar("senha", "ana", "--banco", str(arquivo)) == 0
    tudo = capsys.readouterr()
    for senha in (S1, S2):
        assert senha not in tudo.out
        assert senha not in tudo.err


def test_comando_le_ninho_banco_do_ambiente(arquivo, prompt, monkeypatch, capsys):
    monkeypatch.setenv("NINHO_BANCO", str(arquivo))
    prompt[0].extend([S1, S1])
    assert _rodar("criar", "beto") == 0
    capsys.readouterr()
    assert _rodar("listar") == 0
    assert capsys.readouterr().out.startswith("beto\t(nenhum módulo)\t")


def test_comando_banco_vence_ninho_banco(arquivo, tmp_path, prompt, monkeypatch):
    outro = tmp_path / "outro.db"
    monkeypatch.setenv("NINHO_BANCO", str(outro))
    prompt[0].extend([S1, S1])
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 0
    assert not outro.exists()
    with banco.aberto(arquivo) as conn:
        assert [p["nome"] for p in pessoas.listar(conn)] == ["ana"]


def test_comando_senhas_diferentes_no_prompt_nao_cria(arquivo, prompt, capsys):
    prompt[0].extend([S1, S2])
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert "não são iguais" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []


def test_comando_senha_com_digitacoes_diferentes_mantem_a_antiga(arquivo, prompt, capsys):
    with banco.aberto(arquivo) as conn:
        pessoas.criar(conn, "ana", S1, [])
    prompt[0].extend([S2, S2 + "x"])
    assert _rodar("senha", "ana", "--banco", str(arquivo)) == 1
    assert "nada mudou" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.conferir(conn, "ana", S1) is not None
        assert pessoas.conferir(conn, "ana", S2) is None


def test_comando_senha_curta_e_recusada_na_primeira_digitacao(arquivo, prompt, capsys):
    prompt[0].append("curta")
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert len(prompt[1]) == 1
    assert f"pelo menos {pessoas.SENHA_MINIMA}" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []


def test_comando_nome_repetido_falha_antes_de_pedir_a_senha(arquivo, prompt, capsys):
    with banco.aberto(arquivo) as conn:
        pessoas.criar(conn, "ana", S1, [])
    assert _rodar("criar", "Ana", "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert "já existe" in capsys.readouterr().err


@pytest.mark.parametrize("nome", ["a:b", "", "x" * (pessoas.LIMITE_NOME + 1), "a\tb"])
def test_comando_nome_invalido_falha_antes_de_pedir_a_senha(arquivo, prompt, capsys, nome):
    assert _rodar("criar", nome, "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert capsys.readouterr().err.startswith("erro: o nome")


def test_comando_senha_de_inexistente_falha_antes_de_pedir(arquivo, prompt, capsys):
    assert _rodar("senha", "beto", "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert "ninguém com o nome beto" in capsys.readouterr().err


def test_comando_modulo_desconhecido_falha(arquivo, prompt, capsys):
    assert _rodar("criar", "ana", "--modulos", "agenda", "--banco", str(arquivo)) == 1
    assert prompt[1] == []
    assert "módulo desconhecido: agenda" in capsys.readouterr().err


@pytest.mark.parametrize("palavra", ["nenhum", "Nenhum", ""])
def test_comando_modulos_nenhum_deixa_sem_modulos(arquivo, prompt, palavra):
    prompt[0].extend([S1, S1])
    assert _rodar("criar", "ana", "--modulos", palavra or "lista", "--banco", str(arquivo)) == 0
    assert _rodar("modulos", "ana", palavra, "--banco", str(arquivo)) == 0
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn)[0]["modulos"] == []


def test_comando_sem_banco_falha_e_nao_cria_pasta(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NINHO_BANCO", raising=False)
    assert _rodar("listar") == 1
    assert "NINHO_BANCO" in capsys.readouterr().err
    assert _rodar("listar", "--banco", str(tmp_path / "nao-existe" / "ninho.db")) == 1
    assert not (tmp_path / "nao-existe").exists()


def test_comando_banco_invalido_da_erro_em_portugues(tmp_path, capsys):
    """--banco naming a folder: sqlite cannot open it; the command says so and exits 1."""
    assert _rodar("listar", "--banco", str(tmp_path)) == 1
    assert capsys.readouterr().err.startswith("erro: não deu para usar o banco do app")


def test_comando_entrada_que_termina_no_prompt(arquivo, monkeypatch, capsys):
    def fim(texto=""):
        raise EOFError
    monkeypatch.setattr(comando.getpass, "getpass", fim)
    monkeypatch.setattr(comando, "_terminal", lambda: True)
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert "a entrada terminou antes da senha" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []


def test_comando_sem_terminal_recusa_pedir_a_senha(arquivo, monkeypatch, capsys):
    monkeypatch.setattr(comando.getpass, "getpass", lambda texto="": pytest.fail("pediu a senha"))
    monkeypatch.setattr(comando.sys, "stdin", None)
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert "só é pedida num terminal" in capsys.readouterr().err


def test_comando_sem_acao_e_erro_de_uso():
    with pytest.raises(SystemExit) as saida:
        comando.main(["pessoas"])
    assert saida.value.code != 0


def _processo(*args, entrada=None):
    return subprocess.run([sys.executable, "-m", "ninho", "pessoas", *args], input=entrada,
                          capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)


def test_python_m_ninho_roda_como_processo(arquivo):
    with banco.aberto(arquivo) as conn:
        pessoas.criar(conn, "ana", S1, ["lista"])
    r = _processo("listar", "--banco", str(arquivo))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ana\tlista\t")


@pytest.mark.parametrize("entrada", ["", f"{S1}\n{S1}\n"])
def test_processo_com_stdin_sem_terminal_falha_sem_traceback(arquivo, entrada):
    """Closed or piped stdin: the password is not read from it; Portuguese error, exit 1."""
    r = _processo("criar", "ana", "--banco", str(arquivo), entrada=entrada)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert r.stderr.startswith("erro: a senha só é pedida num terminal")
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []


def test_processo_banco_invalido_falha_sem_traceback(tmp_path):
    r = _processo("listar", "--banco", str(tmp_path))
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert r.stderr.startswith("erro: não deu para usar o banco do app")


def test_comando_ctrl_c_no_prompt_nao_cria(arquivo, monkeypatch, capsys):
    def interromper(texto=""):
        raise KeyboardInterrupt
    monkeypatch.setattr(comando.getpass, "getpass", interromper)
    monkeypatch.setattr(comando, "_terminal", lambda: True)
    assert _rodar("criar", "ana", "--banco", str(arquivo)) == 1
    assert "nada mudou" in capsys.readouterr().err
    with banco.aberto(arquivo) as conn:
        assert pessoas.listar(conn) == []
