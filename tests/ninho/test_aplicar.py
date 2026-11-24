""" "Apply" through the server (df-8x0m.23.13.4): the route asks the engine for the reclassificar job
by its command line, the follow-up route answers the run's state, and the corrected category comes
out in the published table the transactions route reads. No DataForge server is up."""
import polars as pl

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
    assert cliente.get(f"{ROTA}/qualquer").status_code == 401


def test_andamento_de_um_id_desconhecido_e_404(cliente):
    assert cliente.get(f"{ROTA}/nao-existe", auth=AUTH).status_code == 404


def test_esperar_fora_do_limite_e_422(cliente):
    assert cliente.get(f"{ROTA}/qualquer", params={"esperar": 61}, auth=AUTH).status_code == 422


def test_sem_o_motor_a_resposta_e_502_e_diz_por_que(cliente, monkeypatch, tmp_path):
    monkeypatch.setenv("NINHO_DATAFORGE", str(tmp_path / "nao-existe"))
    pedido = cliente.post(ROTA, auth=AUTH)
    assert pedido.status_code == 502
    assert "DataForge" in pedido.json()["detail"]
    assert cliente.get(f"{ROTA}/qualquer", auth=AUTH).status_code == 502


def test_job_que_o_motor_nao_acha_e_502(cliente, workspace):
    (workspace / "jobs" / "reclassificar" / "job.yaml").unlink()
    assert cliente.post(ROTA, auth=AUTH).status_code == 502
