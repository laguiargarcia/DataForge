"""The execution contract (docs/architecture-v2/engine-interface.md, df-8x0m.23.13.1): what a product
calls through the CLI. `run --detach` answers a run id at once and the run goes on by itself;
`run-state` answers the run's state by id; a program reads one JSON object with fixed fields."""
import json
import subprocess
import sys
import time

import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from dataforge import run_request
from dataforge.cli import app
from dataforge.compat import global_db_url
from dataforge.db import JobRunORM, init_db
from dataforge.runlock import workspace_run_lock

runner = CliRunner()
FIELDS = {"run_id", "workspace", "job", "state", "started_at", "finished_at", "failed_task", "reason"}
CLI = [sys.executable, "-m", "dataforge.cli"]


def _workspace(home, monkeypatch, script="print('ok')\n"):
    ws = home / "workspaces" / "ws"
    for d in ("jobs/j", "scripts", "logs"):
        (ws / d).mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "scripts" / "t.py").write_text(script)
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\ntasks:\n  - name: t\n    retry: {attempts: 1, delay_seconds: 0}\n    script: t.py\n")
    monkeypatch.setenv("DATAFORGE_HOME", str(home))
    return ws


def _json(result):
    assert result.stdout.count("\n") == 1, result.stdout
    return json.loads(result.stdout)


def _state(run_id):
    result = runner.invoke(app, ["run-state", run_id])
    assert result.exit_code == 0, result.stdout
    return _json(result)


def _wait_end(run_id, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = _state(run_id)
        if state["finished_at"] is not None:
            return state
        time.sleep(0.2)
    raise AssertionError(f"run never ended: {_state(run_id)}")


def _rows():
    with Session(init_db(global_db_url())) as session:
        return [(r.id, r.status) for r in session.query(JobRunORM).all()]


def test_o_pedido_responde_o_id_antes_de_o_job_terminar_e_a_execucao_segue_sozinha(tmp_path, monkeypatch):
    """The caller is a process that exits right after asking: the run still reaches its end."""
    marker = tmp_path / "ran"
    _workspace(tmp_path, monkeypatch, f"import time, pathlib\ntime.sleep(1.5)\npathlib.Path({str(marker)!r}).write_text('x')\n")
    asked = subprocess.run([*CLI, "run", "ws", "j", "--detach"], capture_output=True, text=True, timeout=60, check=False)
    assert asked.returncode == 0, asked.stderr
    answer = json.loads(asked.stdout)
    assert set(answer) == FIELDS
    assert (answer["workspace"], answer["job"]) == ("ws", "j")
    assert answer["state"] in ("pending", "running")
    assert answer["finished_at"] is None
    assert not marker.exists()  # answered before the job ended

    end = _wait_end(answer["run_id"])
    assert set(end) == FIELDS
    assert (end["state"], end["failed_task"], end["reason"]) == ("success", None, None)
    assert marker.exists()


def test_uma_execucao_que_falha_diz_a_tarefa_e_o_motivo(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch, "import sys\nprint('saldo nao fecha')\nsys.exit(1)\n")
    asked = subprocess.run([*CLI, "run", "ws", "j", "--detach"], capture_output=True, text=True, timeout=60, check=False)
    end = _wait_end(json.loads(asked.stdout)["run_id"])
    assert (end["state"], end["failed_task"]) == ("failed", "t")
    assert "saldo nao fecha" in end["reason"]


@pytest.mark.parametrize("args", [["run", "ws", "nao_existe", "--detach"], ["run", "outro_ws", "j", "--detach"]])
def test_pedido_invalido_sai_com_2_e_nao_deixa_execucao_no_historico(tmp_path, monkeypatch, args):
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, args)
    assert result.exit_code == run_request.EXIT_INVALID_REQUEST == 2
    assert set(_json(result)) == {"error"}
    assert run_request.run_state("qualquer") is None
    assert not (tmp_path / "dataforge.db").exists() or _rows() == []


def test_job_com_yaml_invalido_e_pedido_invalido(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, monkeypatch)
    (ws / "jobs" / "j" / "job.yaml").write_text("name: j\ntasks:\n  - {nome_errado: t}\n")
    result = runner.invoke(app, ["run", "ws", "j", "--detach"])
    assert result.exit_code == 2
    assert "error" in _json(result)


def test_estado_de_um_id_desconhecido_sai_com_2(tmp_path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["run-state", "nao-existe"])
    assert result.exit_code == 2
    assert set(_json(result)) == {"error"}


def test_os_codigos_de_saida_sao_distintos():
    assert len({0, 1, run_request.EXIT_INVALID_REQUEST, run_request.EXIT_BUSY}) == 4


def test_com_o_workspace_ocupado_o_pedido_espera_na_fila_e_o_estado_diz(tmp_path, monkeypatch):
    """Today's rule (df-8x0m.23.15 may change it): the second request waits, and says so."""
    ws = _workspace(tmp_path, monkeypatch)
    with workspace_run_lock(ws, "outro-run"):
        asked = subprocess.run([*CLI, "run", "ws", "j", "--detach"], capture_output=True, text=True, timeout=60, check=False)
        run_id = json.loads(asked.stdout)["run_id"]
        deadline = time.monotonic() + 30
        while _state(run_id)["state"] != "waiting":
            assert time.monotonic() < deadline, _state(run_id)
            time.sleep(0.2)
        assert _state(run_id)["reason"] is None  # the queue marker is not shown as a failure reason
    assert _wait_end(run_id)["state"] == "success"


def test_run_sem_a_opcao_nao_muda(tmp_path, monkeypatch):
    """`dataforge run <ws> <job>` (what the OS scheduler calls) still waits and prints text."""
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["run", "ws", "j"])
    assert result.exit_code == 0
    assert "t: OK" in result.stdout
    assert "run_id" not in result.stdout
    assert [status for _, status in _rows()] == ["success"]
