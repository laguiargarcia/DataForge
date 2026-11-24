"""The walking skeleton end to end (df-8x0m.23.13.6): the one command that brings it up
(scripts/dev/esqueleto.py) runs finance_demo on a scratch copy and serves ninho as a process of its
own, with a person it creates in its app database from NINHO_ESQUELETO_NOME and
NINHO_ESQUELETO_SENHA; the test talks to it only over HTTP, logged in as that person: it reads the month, corrects a
category (app database), applies (the engine, by its command line), follows the run and reads the
corrected category back; and it adds a shopping-list item, marks it bought and reads it back
(NINHO_BANCO, a file in the skeleton's own folder). No TestClient and no DataForge server: the four
pieces talk for real."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import polars as pl
import pytest

from ninho.financas import leitura
from tests.ninho.conftest import ANA

ESQUELETO = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "esqueleto.py"
_SUBIDA = 600       # seconds for the copy, finance_demo and the server to come up (Windows is slower)
_PARADA = 30        # seconds for a graceful stop before the process is killed


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parar(processo: subprocess.Popen) -> int:
    """Stop the skeleton the way a person does (Ctrl+C / Ctrl+Break), killing it only if it hangs."""
    if processo.poll() is None:
        processo.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        try:
            processo.wait(_PARADA)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait(_PARADA)
    return processo.returncode


@pytest.fixture
def esqueleto(tmp_path):
    """The skeleton up on a free port, over a copy under tmp_path; (its base URL, its home, the process, its log)."""
    home, log = tmp_path / "home", tmp_path / "esqueleto.log"
    porta = _porta_livre()
    ambiente = {k: v for k, v in os.environ.items() if k not in ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG")}
    with log.open("w", encoding="utf-8") as saida:
        processo = subprocess.Popen(
            [sys.executable, str(ESQUELETO), "--porta", str(porta), "--home", str(home)],
            env={**ambiente, "NINHO_ESQUELETO_NOME": ANA[0], "NINHO_ESQUELETO_SENHA": ANA[1],
                 "PYTHONIOENCODING": "utf-8"}, stdout=saida, stderr=saida,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
    base = f"http://127.0.0.1:{porta}"
    try:
        prazo = time.monotonic() + _SUBIDA
        while True:
            assert processo.poll() is None, log.read_text(encoding="utf-8")[-4000:]
            try:
                httpx.get(f"{base}/", timeout=2)
                break
            except httpx.TransportError:
                assert time.monotonic() < prazo, log.read_text(encoding="utf-8")[-4000:]
                time.sleep(0.5)
        yield base, home, processo, log
    finally:
        _parar(processo)


def test_o_esqueleto_le_corrige_aplica_e_mostra_o_resultado(esqueleto):
    base, home, processo, log = esqueleto
    workspace = home / "workspaces" / "GetFinance"
    with httpx.Client(base_url=base, auth=ANA, timeout=120) as http:
        assert httpx.get(f"{base}/api/financas/edicoes").status_code == 401   # nobody in without a login
        assert httpx.get(f"{base}/api/eu", auth=(ANA[0], "senha-errada")).status_code == 401
        assert http.get("/api/eu").json() == {"nome": ANA[0], "modulos": ["financas", "lista"]}
        pagina = http.get("/")
        assert pagina.status_code == 200   # the screen comes from the same server
        assert "<html" in pagina.text.lower()

        # Read: the transactions of a month of the synthetic run, through the domain route.
        anomes = pl.read_delta(str(workspace / "data" / "curated" / leitura.TABELA))["anomes_key"].max()
        mes = f"{anomes // 100}-{anomes % 100:02d}"
        linhas = http.get("/api/financas/transacoes", params={"mes": mes}).json()["transacoes"]
        assert linhas, f"nenhuma transação em {mes}"
        alvo = linhas[0]

        # Correct: one category, saved in the app database; the published table waits for "apply".
        edicoes = http.get("/api/financas/edicoes").json()
        nova = sorted(c["subcategoria"] for c in edicoes["categorias"] if c["subcategoria"] != alvo["subcategoria"])[0]
        assert http.put(f"/api/financas/correcoes/{alvo['id']}", json={"subcategoria": nova}).status_code == 200
        assert http.get("/api/financas/edicoes").json()["pendente"] is True
        antes = next(linha for linha in http.get("/api/financas/transacoes", params={"mes": mes}).json()["transacoes"]
                     if linha["id"] == alvo["id"])
        assert antes["subcategoria"] == alvo["subcategoria"]

        # Apply: the server asks the engine for the job and follows the run until it ends.
        pedido = http.post("/api/financas/aplicar")
        assert pedido.status_code == 202, pedido.text
        fim = http.get(f"/api/financas/aplicar/{pedido.json()['run_id']}", params={"esperar": 60})
        for _ in range(9):   # up to 10 minutes on a slow runner
            if fim.json()["finished_at"] is not None:
                break
            fim = http.get(f"/api/financas/aplicar/{pedido.json()['run_id']}", params={"esperar": 60})
        assert (fim.json()["state"], fim.json()["failed_task"]) == ("success", None), fim.text

        # The result: the published table has the corrected category and nothing is pending.
        depois = next(linha for linha in http.get("/api/financas/transacoes", params={"mes": mes}).json()["transacoes"]
                      if linha["id"] == alvo["id"])
        assert (depois["subcategoria"], depois["corrigida"]) == (nova, True)
        assert http.get("/api/financas/edicoes").json()["pendente"] is False

        # The list: an item there and back, in the app database the skeleton keeps in its own folder.
        criado = http.post("/api/lista/itens", json={"texto": "biscoito"})
        assert criado.status_code == 201, criado.text
        marcado = http.patch(f"/api/lista/itens/{criado.json()['id']}", json={"comprado": True})
        assert marcado.status_code == 200, marcado.text
        assert [(i["texto"], i["comprado"], i["adicionado_por"]) for i in http.get("/api/lista/itens").json()["itens"]] == [
            ("biscoito", True, ANA[0])]
        assert (home / "ninho.db").is_file()

    assert _parar(processo) == 0, log.read_text(encoding="utf-8")[-4000:]   # stops cleanly, nothing left up
