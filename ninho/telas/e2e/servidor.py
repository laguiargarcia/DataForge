"""The ninho server for the screen's browser test (playwright.config.ts starts it): on a scratch copy
of the GetFinance workspace after the synthetic run (finance_demo), never on the checkout's own
workspace nor on real data. The copy is removed when the test run stops the server.

Usage: python servidor.py <port> <password>
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
WORKSPACE = RAIZ / "workspaces" / "GetFinance"
# Never reach a job the server starts: they point the pipeline at real data.
_FORA = ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG")


def main(porta: str, senha: str) -> int:
    ambiente = {k: v for k, v in os.environ.items() if k not in _FORA}  # noqa: TID251 -- the children get this environment without the real-data variables
    with tempfile.TemporaryDirectory(prefix="ninho-telas-") as home:
        copia = Path(home) / "workspaces" / "GetFinance"
        shutil.copytree(WORKSPACE, copia,
                        ignore=shutil.ignore_patterns("data", "tests", "apps", ".pytest_cache", "__pycache__"))
        subprocess.run([sys.executable, "-m", "dataforge", "run", "GetFinance", "finance_demo"], cwd=home,
                       env={**ambiente, "DATAFORGE_HOME": home}, stdout=sys.stderr, check=True)
        servidor = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "ninho.servidor.app:app", "--host", "127.0.0.1", "--port", porta],
            cwd=RAIZ, env={**ambiente, "NINHO_WORKSPACE": str(copia), "NINHO_SENHA": senha})
        signal.signal(signal.SIGTERM, lambda *_: servidor.terminate())
        signal.signal(signal.SIGINT, lambda *_: servidor.terminate())
        return servidor.wait()


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
