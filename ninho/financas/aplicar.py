""" "Apply": ask the engine for the job that redoes the categories and the numbers after an edit,
and follow that run (docs/architecture-v2/engine-interface.md).

ninho talks to DataForge only through its command line: `run --detach` answers a run id, and
`run-state --wait` follows the run with one process. Starting the command costs about a second on
Windows, so a caller asks to wait instead of asking again and again (df-8x0m.23.17).
"""
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

JOB = "reclassificar"
_SAIDA_PEDIDO_INVALIDO = 2      # the engine's exit code for an invalid request or an unknown run id
_SAIDA_AINDA_RODANDO = 4        # `run-state --wait --timeout`: the time ran out before the run ended
_SEGUNDOS_DO_COMANDO = 120      # the command itself answering, beyond any wait asked of it


class SemMotor(Exception):
    """The engine did not answer as the contract says: not installed, crashed, or not JSON."""


class Desconhecida(Exception):
    """The engine knows no run with that id."""


def _comando() -> list[str]:
    """The tokens that start DataForge: ``NINHO_DATAFORGE`` when set, else the `dataforge` installed
    next to this interpreter."""
    definido = os.environ.get("NINHO_DATAFORGE")  # noqa: TID251 -- ninho reads its own environment; dataforge.compat is the engine's
    if definido:
        return [definido]
    script = Path(sysconfig.get_path("scripts")) / ("dataforge.exe" if os.name == "nt" else "dataforge")
    return [str(script)] if script.is_file() else [sys.executable, "-m", "dataforge.cli"]


def _chamar(workspace: Path, argumentos: list[str], espera: float = 0) -> tuple[int, dict]:
    """Run one engine command for this workspace; its exit code and the JSON object it printed."""
    # The engine finds a workspace by name under <home>/workspaces (a registry of folders is still
    # to come, engine-interface.md), so its home is two folders above the workspace.
    ambiente = {**os.environ, "DATAFORGE_HOME": str(workspace.parents[1])}  # noqa: TID251 -- the child gets this process's environment plus the engine's home
    try:
        feito = subprocess.run([*_comando(), *argumentos], env=ambiente, capture_output=True, text=True,
                               encoding="utf-8", timeout=_SEGUNDOS_DO_COMANDO + espera, check=False)
        resposta = json.loads(feito.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as erro:
        raise SemMotor(f"{type(erro).__name__}: {erro}") from erro
    if not isinstance(resposta, dict):
        raise SemMotor(f"resposta inesperada: {resposta!r}")
    return feito.returncode, resposta


def pedir(workspace: Path) -> dict:
    """Ask for the apply job; the engine's answer, with the run id to follow."""
    saida, resposta = _chamar(workspace, ["run", "--detach", "--", workspace.name, JOB])
    if saida == _SAIDA_PEDIDO_INVALIDO or "run_id" not in resposta:
        raise SemMotor(str(resposta.get("error", resposta)))
    if resposta.get("started_at") and resposta.get("state") == "failed" and resposta.get("failed_task") is None:
        # Ended as failed with no task behind it: the run's process never started.
        raise SemMotor(str(resposta.get("reason")))
    return resposta


def andamento(workspace: Path, run_id: str, esperar: float = 0) -> dict:
    """The state of a run. With ``esperar`` > 0 the engine answers when the run has ended, or after
    that many seconds with the state so far."""
    opcoes = ["--wait", "--timeout", str(esperar)] if esperar > 0 else []
    # "--" ends the options: the id comes from a URL and must never be read as one.
    saida, resposta = _chamar(workspace, ["run-state", *opcoes, "--", run_id], esperar)
    if saida == _SAIDA_PEDIDO_INVALIDO:
        raise Desconhecida(run_id)
    if saida not in (0, _SAIDA_AINDA_RODANDO) or "state" not in resposta:
        raise SemMotor(str(resposta))
    return resposta
