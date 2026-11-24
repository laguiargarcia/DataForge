""" "Apply" through the server (df-8x0m.23.13.4): the route asks the engine for the reclassificar job
by its command line, the follow-up route answers the run's state, and the corrected category comes
out in the published table the transactions route reads. No DataForge server is up."""
import uuid

import polars as pl
import pytest

from ninho.financas import leitura
from tests.ninho.conftest import AUTH

ROTA = "/api/financas/aplicar"


def _corrigir_uma(cliente, workspace) -> tuple[str, str, str]:
    """Correct the category of one transaction; (its id, its month, the new category)."""
    linha = pl.read_delta(str(workspace / "data" / "curated" / leitura.TABELA)).sort("id").row(0, named=True)
    categorias = cliente.get("/api/financas/edicoes", auth=AUTH).json()["categorias"]
    nova = sorted(c["subcategoria"] for c in categorias if c["subcategoria"] != linha["subcategoria"])[0]
    r = cliente.put(f"/api/financas/correcoes/{linha['id']}", json={"subcategoria": nova}, auth=AUTH)
    assert r.status_code == 200, r.text
    return linha["id"], f"{linha['anomes_key'] // 100}-{linha['anomes_key'] % 100:02d}", nova


def test_aplicar_devolve_um_id_o_andamento_chega_a_sucesso_e_a_categoria_nova_aparece(cliente, workspace):
    transacao, mes, nova = _corrigir_uma(cliente, workspace)
    assert cliente.get("/api/financas/edicoes", auth=AUTH).json()["pendente"] is True

    pedido = cliente.post(ROTA, auth=AUTH)
    assert pedido.status_code == 202, pedido.text
    run_id = pedido.json()["run_id"]
    assert pedido.json()["job"] == "reclassificar"

    fim = cliente.get(f"{ROTA}/{run_id}", params={"esperar": 60}, auth=AUTH)
    assert fim.status_code == 200, fim.text
    assert (fim.json()["state"], fim.json()["failed_task"]) == ("success", None)
    agora = cliente.get(f"{ROTA}/{run_id}", auth=AUTH)   # without waiting, the same end state
    assert agora.json()["state"] == "success"

    linhas = cliente.get("/api/financas/transacoes", params={"mes": mes}, auth=AUTH).json()["transacoes"]
    corrigida = next(linha for linha in linhas if linha["id"] == transacao)
    assert (corrigida["subcategoria"], corrigida["corrigida"]) == (nova, True)
    assert cliente.get("/api/financas/edicoes", auth=AUTH).json()["pendente"] is False


def test_as_rotas_de_aplicar_pedem_a_senha(cliente):
    assert cliente.post(ROTA).status_code == 401
    assert cliente.get(f"{ROTA}/{uuid.uuid4()}").status_code == 401


def test_andamento_de_um_id_desconhecido_e_404(cliente):
    assert cliente.get(f"{ROTA}/{uuid.uuid4()}", auth=AUTH).status_code == 404


@pytest.mark.parametrize("run_id", ["--help", "-x", "--wait", "nao-e-um-id"])
def test_id_que_nao_e_um_uuid_nem_chega_ao_motor(cliente, monkeypatch, run_id):
    """An id from the URL never becomes an option of the engine's command."""
    monkeypatch.setattr("ninho.financas.aplicar._chamar", lambda *a, **k: pytest.fail("chamou o motor"))
    assert cliente.get(f"{ROTA}/{run_id}", auth=AUTH).status_code == 422


def test_esperar_fora_do_limite_e_422(cliente):
    assert cliente.get(f"{ROTA}/{uuid.uuid4()}", params={"esperar": 61}, auth=AUTH).status_code == 422


def test_sem_o_motor_a_resposta_e_502_e_diz_por_que(cliente, monkeypatch, tmp_path):
    monkeypatch.setenv("NINHO_DATAFORGE", str(tmp_path / "nao-existe"))
    pedido = cliente.post(ROTA, auth=AUTH)
    assert pedido.status_code == 502
    assert "DataForge" in pedido.json()["detail"]
    assert str(tmp_path) not in pedido.text   # the path of the machine stays in the server's log
    assert cliente.get(f"{ROTA}/{uuid.uuid4()}", auth=AUTH).status_code == 502


def test_job_que_o_motor_nao_acha_e_502(cliente, workspace):
    (workspace / "jobs" / "reclassificar" / "job.yaml").unlink()
    assert cliente.post(ROTA, auth=AUTH).status_code == 502


def _trocar_job(workspace, script: str) -> None:
    """Make the apply job one task that runs `script`."""
    (workspace / "scripts" / "t_aplicar.py").write_text(script, encoding="utf-8")
    (workspace / "jobs" / "reclassificar" / "job.yaml").write_text(
        "name: reclassificar\ntasks:\n  - name: t\n    retry: {attempts: 1, delay_seconds: 0}\n    script: t_aplicar.py\n",
        encoding="utf-8")


def test_job_que_falha_responde_200_com_o_estado_a_tarefa_e_o_motivo(cliente, workspace):
    _trocar_job(workspace, "import sys\nprint('saldo nao fecha')\nsys.exit(1)\n")
    pedido = cliente.post(ROTA, auth=AUTH)
    assert pedido.status_code == 202, pedido.text
    fim = cliente.get(f"{ROTA}/{pedido.json()['run_id']}", params={"esperar": 60}, auth=AUTH)
    assert fim.status_code == 200
    assert (fim.json()["state"], fim.json()["failed_task"]) == ("failed", "t")
    assert "saldo nao fecha" in fim.json()["reason"]


def test_espera_que_acaba_antes_do_job_responde_o_estado_em_andamento(cliente, workspace):
    _trocar_job(workspace, "import time\ntime.sleep(4)\n")
    run_id = cliente.post(ROTA, auth=AUTH).json()["run_id"]
    cedo = cliente.get(f"{ROTA}/{run_id}", params={"esperar": 0.3}, auth=AUTH)
    assert cedo.status_code == 200
    assert cedo.json()["finished_at"] is None
    fim = cliente.get(f"{ROTA}/{run_id}", params={"esperar": 60}, auth=AUTH)
    assert fim.json()["state"] == "success"


def test_execucao_cujo_processo_nao_subiu_e_502_e_nao_um_aceite(cliente, monkeypatch):
    nao_subiu = {"run_id": str(uuid.uuid4()), "state": "failed", "started_at": "2026-01-01T00:00:00",
                 "finished_at": "2026-01-01T00:01:00", "failed_task": None, "reason": "did not start"}
    monkeypatch.setattr("ninho.financas.aplicar._chamar", lambda *a, **k: (1, nao_subiu))
    assert cliente.post(ROTA, auth=AUTH).status_code == 502


def test_workspace_dado_como_caminho_relativo_funciona(cliente, workspace, monkeypatch):
    monkeypatch.chdir(workspace.parent)
    monkeypatch.setenv("NINHO_WORKSPACE", workspace.name)
    assert cliente.get(f"{ROTA}/{uuid.uuid4()}", auth=AUTH).status_code == 404
