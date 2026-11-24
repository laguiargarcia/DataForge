"""Bring the ninho walking skeleton up with one command (df-8x0m.23.13.6): copy the GetFinance
workspace to a scratch home, run the synthetic job (finance_demo) there with DataForge, and serve the
ninho server (routes and screen) over that copy. Never the checkout's own workspace, never real data.
The app database (NINHO_BANCO, the shopping list) is a file in the same folder, <home>/ninho.db.

Used by hand, by the end-to-end test (tests/ninho/test_esqueleto.py) and by the screen's browser test
(ninho/telas/playwright.config.ts). The password comes from NINHO_SENHA, as the server reads it.

Usage: NINHO_SENHA=<a password> python scripts/dev/esqueleto.py [--endereco 127.0.0.1] [--porta 8100]
       [--home <folder>]

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

from ninho.servidor.app import app

RAIZ = Path(__file__).resolve().parents[2]
WORKSPACE = RAIZ / "workspaces" / "GetFinance"
# Never reach a job the server starts: they point the pipeline at real data.
_FORA = ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG")


def preparar(home: Path) -> Path:
    """The GetFinance workspace copied under <home>/workspaces, after finance_demo ran there."""
    copia = home / "workspaces" / "GetFinance"
    shutil.copytree(WORKSPACE, copia,
                    ignore=shutil.ignore_patterns("data", "tests", "apps", ".pytest_cache", "__pycache__"))
    subprocess.run([sys.executable, "-m", "dataforge", "run", "GetFinance", "finance_demo"], cwd=home,
                   env={**os.environ, "DATAFORGE_HOME": str(home)}, stdout=sys.stderr, check=True)
    return copia


def _parar(*_) -> None:
    """Stop by unwinding, with exit code 0, so the temporary copy is removed: uvicorn re-raises the
    signal it caught after shutting down, and the default handlers would end the process on the spot
    (SIGTERM) or with a traceback (SIGINT)."""
    raise SystemExit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--endereco", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--porta", type=int, default=8100, help="port (default 8100)")
    parser.add_argument("--home", type=Path, help="folder for the copy (default: a temporary one, removed at the end)")
    args = parser.parse_args(argv)
    if not os.environ.get("NINHO_SENHA"):
        parser.error("defina NINHO_SENHA: a senha que o servidor pede")
    for nome in _FORA:
        os.environ.pop(nome, None)
    for nome in ("SIGINT", "SIGTERM", "SIGBREAK"):   # SIGBREAK: Ctrl+Break, Windows only
        if hasattr(signal, nome):
            signal.signal(getattr(signal, nome), _parar)
    if args.home:
        args.home.mkdir(parents=True, exist_ok=True)
    with nullcontext(args.home) if args.home else tempfile.TemporaryDirectory(prefix="ninho-esqueleto-", ignore_cleanup_errors=True) as home:
        os.environ["NINHO_WORKSPACE"] = str(preparar(Path(home)))
        os.environ["NINHO_BANCO"] = str(Path(home) / "ninho.db")
        uvicorn.run(app, host=args.endereco, port=args.porta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
