"""The ninho command, run on the server machine: ``python -m ninho pessoas <criar|senha|modulos|listar>``.

The only way to create a person or change a password (docs/architecture-v2/people-and-visibility.md):
the password is asked at the prompt, twice, never taken as an argument nor read from a pipe (the
command refuses when its input is not a terminal). The app database is
``--banco`` or ``NINHO_BANCO``.
"""
import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

from ninho.servidor import banco, pessoas


class Erro(Exception):
    """Something the person running the command has to fix; its text is shown as it is."""


NENHUM = "nenhum"   # a word for no module: PowerShell 5.1 may drop an empty argument given to an exe


def _modulos(texto: str) -> list[str]:
    return [] if texto.strip().casefold() in ("", NENHUM) else texto.split(",")


def _arquivo(opcao: str | None) -> Path:
    if opcao is None:
        try:
            return banco.caminho()
        except banco.SemBanco as erro:
            raise Erro(f"{erro}: use --banco ou defina NINHO_BANCO") from erro
    if not Path(opcao).parent.is_dir():
        raise Erro("a pasta de --banco não existe")
    return Path(opcao)


def _terminal() -> bool:
    return sys.stdin is not None and sys.stdin.isatty()


def _pedir_senha() -> str:
    """The password typed twice at the prompt. Refused without a terminal: getpass would then read
    it from a pipe with echo on Linux, and from the console, not the pipe, on Windows."""
    if not _terminal():
        raise Erro("a senha só é pedida num terminal, e a entrada deste comando não é um; nada mudou")
    senha = pessoas.validar_senha(getpass.getpass("Senha: "))
    if getpass.getpass("Repita a senha: ") != senha:
        raise Erro("as duas senhas não são iguais; nada mudou")
    return senha


def _linha(pessoa: dict) -> str:
    return f"{pessoa['nome']}\t{','.join(pessoa['modulos']) or '(nenhum módulo)'}\t{pessoa['criada_em']}"


def _parser() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--banco", help="o arquivo do banco do app (padrão: NINHO_BANCO)")
    raiz = argparse.ArgumentParser(prog="python -m ninho", description="Comandos do ninho.")
    grupos = raiz.add_subparsers(dest="grupo", required=True)
    acoes = grupos.add_parser("pessoas", help="quem pode entrar no ninho").add_subparsers(dest="acao", required=True)
    criar = acoes.add_parser("criar", parents=[comum], help="cria uma pessoa; a senha é pedida no prompt")
    criar.add_argument("nome")
    criar.add_argument("--modulos", default="", help=f"separados por vírgula, entre: {','.join(pessoas.MODULOS)}")
    acoes.add_parser("senha", parents=[comum], help="troca a senha; a nova é pedida no prompt").add_argument("nome")
    modulos = acoes.add_parser("modulos", parents=[comum], help="define os módulos (substitui os de antes)")
    modulos.add_argument("nome")
    modulos.add_argument("modulos", help=f"separados por vírgula, entre: {','.join(pessoas.MODULOS)}; '{NENHUM}' para nenhum")
    acoes.add_parser("sair", parents=[comum], help="encerra as sessões da pessoa em todos os aparelhos").add_argument("nome")
    acoes.add_parser("listar", parents=[comum], help="mostra as pessoas, sem senha")
    return raiz


def _executar(args: argparse.Namespace) -> None:
    with banco.aberto(_arquivo(args.banco)) as conn:
        if args.acao == "listar":
            todas = pessoas.listar(conn)
            print("\n".join(_linha(p) for p in todas) if todas else "nenhuma pessoa")
        elif args.acao == "criar":
            nome = pessoas.validar_nome(args.nome)
            modulos = pessoas.modulos_validos(_modulos(args.modulos))
            if pessoas.existe(conn, nome):
                raise pessoas.Repetido(f"já existe alguém com o nome {nome}")
            print(f"criada: {_linha(pessoas.criar(conn, nome, _pedir_senha(), modulos))}")
        elif args.acao == "senha":
            if not pessoas.existe(conn, args.nome):
                raise pessoas.Ausente(args.nome.strip())
            pessoas.trocar_senha(conn, args.nome, _pedir_senha())
            print(f"senha trocada: {args.nome.strip()} (as sessões abertas foram encerradas)")
        elif args.acao == "sair":
            print(f"sessões encerradas: {pessoas.sair(conn, args.nome)}")
        else:
            print(f"módulos definidos: {_linha(pessoas.definir_modulos(conn, args.nome, _modulos(args.modulos)))}")


def main(argv: list[str] | None = None) -> int:
    """Run the command; 0 when it worked, 1 with the reason on stderr when it did not."""
    args = _parser().parse_args(argv)
    try:
        _executar(args)
    except pessoas.Ausente as erro:
        print(f"erro: ninguém com o nome {erro}", file=sys.stderr)
        return 1
    except (Erro, pessoas.Invalido, pessoas.Repetido) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelado; nada mudou", file=sys.stderr)
        return 1
    except EOFError:
        print("\nerro: a entrada terminou antes da senha; nada mudou", file=sys.stderr)
        return 1
    except sqlite3.Error as erro:
        print(f"erro: não deu para usar o banco do app ({type(erro).__name__}: {erro})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
