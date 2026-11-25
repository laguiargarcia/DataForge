"""Bring the ninho walking skeleton up with one command (df-8x0m.23.13.6): copy the GetFinance
workspace to a scratch home, run the synthetic job (finance_demo) there with DataForge, and serve the
ninho server (routes and screen) over that copy. Never the checkout's own workspace, never real data.
The app database (NINHO_BANCO: the people, the shopping list) is a file in the same folder,
<home>/ninho.db, and gets one person with every module, whose name and password come from
NINHO_ESQUELETO_NOME and NINHO_ESQUELETO_SENHA: the login the server asks for. They are read only
here and taken out of the environment, so neither finance_demo nor the server sees them. Run again
on the same --home, the person gets that password and every module. Other people, each with one
module, are created when both their variables are set (_OUTRAS): NINHO_ESQUELETO_NOME_LISTA and
NINHO_ESQUELETO_SENHA_LISTA, the shopping list only (the screen's browser test uses it to see the bar
of someone without finances); NINHO_ESQUELETO_NOME_DESEJOS and NINHO_ESQUELETO_SENHA_DESEJOS, the
wishlist only (a second person who marks the surprise gift on the first one's wish).

Used by hand, by the end-to-end test (tests/ninho/test_esqueleto.py) and by the screen's browser test
(ninho/telas/playwright.config.ts).

Usage: NINHO_ESQUELETO_NOME=<a name> NINHO_ESQUELETO_SENHA=<a password> python scripts/dev/esqueleto.py
       [--endereco 127.0.0.1] [--porta 8100] [--home <folder>]

Without --home the copy lives in a temporary folder, removed when the server stops (Ctrl+C, SIGTERM,
or Ctrl+Break on Windows). With --home it goes there and stays.
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

import uvicorn

from ninho.servidor import banco, pessoas
from ninho.servidor.app import app

RAIZ = Path(__file__).resolve().parents[2]
WORKSPACE = RAIZ / "workspaces" / "GetFinance"
# Never reach a job the server starts: they point the pipeline at real data.
_FORA = ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG")
# The skeleton's other people: the suffix of their two variables and their one module.
_OUTRAS = (("_LISTA", ("lista",)), ("_DESEJOS", ("desejos",)))


def preparar(home: Path) -> Path:
    """The GetFinance workspace copied under <home>/workspaces, after finance_demo ran there."""
    copia = home / "workspaces" / "GetFinance"
    shutil.copytree(WORKSPACE, copia,
                    ignore=shutil.ignore_patterns("data", "tests", "apps", ".pytest_cache", "__pycache__"))
    subprocess.run([sys.executable, "-m", "dataforge", "run", "GetFinance", "finance_demo"], cwd=home,
                   env={**os.environ, "DATAFORGE_HOME": str(home)}, stdout=sys.stderr, check=True)
    return copia


def criar_pessoa(arquivo: Path, nome: str, senha: str, modulos: tuple[str, ...] = pessoas.MODULOS) -> None:
    """A person of the skeleton in the app database at <arquivo>, with <senha> and <modulos> (every
    module unless told)."""
    with banco.aberto(arquivo) as conn:
        if pessoas.existe(conn, nome):
            pessoas.trocar_senha(conn, nome, senha)
            pessoas.definir_modulos(conn, nome, list(modulos))
        else:
            pessoas.criar(conn, nome, senha, list(modulos))


def _parar(*_) -> None:
    """Stop by unwinding, with exit code 0, so the temporary copy is removed: uvicorn re-raises the
    signal it caught after shutting down, and the default handlers would end the process on the spot
    (SIGTERM) or with a traceback (SIGINT)."""
    raise SystemExit(0)


def _logins(parser: argparse.ArgumentParser) -> tuple[tuple[str, str], list[tuple[tuple[str, str], tuple[str, ...]]]]:
    """The first person's (name, password) and the other people's ((name, password), modules), read
    from the environment and taken out of it: neither finance_demo nor the server and the jobs it
    starts get them. Checked here, before the copy and finance_demo, which take a while."""
    login = os.environ.pop("NINHO_ESQUELETO_NOME", ""), os.environ.pop("NINHO_ESQUELETO_SENHA", "")
    lidas = [((os.environ.pop(f"NINHO_ESQUELETO_NOME{sufixo}", ""), os.environ.pop(f"NINHO_ESQUELETO_SENHA{sufixo}", "")),
              sufixo, modulos) for sufixo, modulos in _OUTRAS]
    if not all(login):
        parser.error("defina NINHO_ESQUELETO_NOME e NINHO_ESQUELETO_SENHA: o login que o servidor pede")
    for outra, sufixo, _ in lidas:
        if any(outra) and not all(outra):
            parser.error(f"NINHO_ESQUELETO_NOME{sufixo} e NINHO_ESQUELETO_SENHA{sufixo} vão juntas")
    outras = [(outra, modulos) for outra, _, modulos in lidas if all(outra)]
    try:
        for nome, senha in [login, *(outra for outra, _ in outras)]:
            pessoas.validar_nome(nome)
            pessoas.validar_senha(senha)
    except pessoas.Invalido as erro:
        parser.error(str(erro))
    return login, outras


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--endereco", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--porta", type=int, default=8100, help="port (default 8100)")
    parser.add_argument("--home", type=Path, help="folder for the copy (default: a temporary one, removed at the end)")
    args = parser.parse_args(argv)
    login, outras = _logins(parser)
    for nome in _FORA:
        os.environ.pop(nome, None)
    for nome in ("SIGINT", "SIGTERM", "SIGBREAK"):   # SIGBREAK: Ctrl+Break, Windows only
        if hasattr(signal, nome):
            signal.signal(getattr(signal, nome), _parar)
    if args.home:
        args.home.mkdir(parents=True, exist_ok=True)
    with nullcontext(args.home) if args.home else tempfile.TemporaryDirectory(prefix="ninho-esqueleto-", ignore_cleanup_errors=True) as home:
        os.environ["NINHO_WORKSPACE"] = str(preparar(Path(home)))
        banco_do_app = Path(home) / "ninho.db"
        os.environ["NINHO_BANCO"] = str(banco_do_app)
        criar_pessoa(banco_do_app, *login)
        for outra, modulos in outras:
            criar_pessoa(banco_do_app, *outra, modulos=modulos)
        uvicorn.run(app, host=args.endereco, port=args.porta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
