"""Conferência pelo saldo (df-z4fh.1): por conta BANK e por intervalo entre dois snapshots seguidos de
raw/accounts, a variação do saldo menos a soma dos lançamentos de cleansed.transactions do intervalo.

A função pura (scripts/report/conferencia_saldo.py) e a task que grava curated.conferencia_saldo.
Dados falsos: nenhum id, nome ou valor vem do dado real.
"""
import json
import sys
from datetime import date, datetime, timezone

import polars as pl
import pytest

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from conferencia_saldo import (EM_ABERTO, ESTADOS, FECHA, FECHA_COM_VIZINHO, HISTORICO_INCOMPLETO, NAO_FECHA,
                               TOLERANCIA, conferencia_saldo)

BANK, OUTRA, CARTAO = "p-bank-1", "p-bank-2", "p-card-1"


def _snap(account, updated_at, balance, type_="BANK", ingested_at=None):
    # raw/accounts é todo texto, como o landing2raw grava.
    return {"id": account, "type": type_, "balance": str(balance), "updatedAt": updated_at,
            "ingested_at": ingested_at or updated_at[:10]}


def _accounts(*rows):
    return pl.DataFrame(list(rows), schema={c: pl.Utf8 for c in rows[0]})


def _txn(id_, account, day, amount, **campos):
    # campos: status (POSTED) e description (uma compra fictícia), como em cleansed.
    return {"id": id_, "accountId": account, "date": date.fromisoformat(day), "amount": amount,
            "status": "POSTED", "description": "COMPRA FICTICIA", **campos}


_TXN_SCHEMA = {"id": pl.Utf8, "accountId": pl.Utf8, "date": pl.Date, "amount": pl.Float64,
               "status": pl.Utf8, "description": pl.Utf8}
_RAW_SCHEMA = {"id": pl.Utf8, "date": pl.Utf8, "ingested_at": pl.Utf8}


def _transactions(*rows):
    return pl.DataFrame(list(rows), schema=_TXN_SCHEMA)


def _raw(*rows):
    # raw/transactions: (id, date com hora, ingested_at), todo texto.
    return pl.DataFrame(list(rows), schema=_RAW_SCHEMA, orient="row")


def _conferir(accounts, transactions, raw=None):
    return conferencia_saldo(accounts, transactions, _raw() if raw is None else raw)


def _utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


# Dois snapshots de um dia de diferença, às 19h UTC (a hora típica do sync).
DE, ATE = "2026-06-01T19:00:00.000Z", "2026-06-02T19:00:00.000Z"


def _serie(*saldos):
    # Snapshots diários da conta BANK às 19h UTC a partir de 01/06, com os saldos dados.
    return _accounts(*[_snap(BANK, f"2026-06-{d:02d}T19:00:00.000Z", s) for d, s in enumerate(saldos, 1)])


def test_intervalo_que_fecha():
    out = _conferir(_accounts(_snap(BANK, DE, 100.0), _snap(BANK, ATE, 70.0)),
                    _transactions(_txn("t1", BANK, "2026-06-02", -30.0)))
    assert out.to_dicts() == [{
        "account_id": BANK, "de": _utc(DE), "ate": _utc(ATE), "coletado_em": date(2026, 6, 2),
        "saldo_de": 100.0, "saldo_ate": 70.0,
        "variacao_saldo": -30.0, "soma_lancamentos": -30.0, "n_lancamentos": 1, "diferenca": 0.0,
        "diferenca_acumulada": 0.0, "estado": FECHA,
    }]


def test_linha_de_aplicacao_automatica_nao_entra_na_soma():
    # df-7twv.2: o saldo é o total (conta + aplicação); aplicar ou resgatar automático não o muda.
    out = _conferir(_accounts(_snap(BANK, DE, 100.0), _snap(BANK, ATE, 70.0)),
                    _transactions(_txn("t1", BANK, "2026-06-02", -30.0),
                                  _txn("a1", BANK, "2026-06-02", -500.0, description="APL APLIC AUT FICT"),
                                  _txn("r1", BANK, "2026-06-02", 200.0, description="Resgate RES APLIC AUT FICT")))
    assert out.select("soma_lancamentos", "n_lancamentos", "diferenca", "estado").row(0) == (-30.0, 1, 0.0, FECHA)


def test_intervalo_com_lancamento_faltando():
    # O saldo caiu 50 e só 30 de lançamentos explicam: falta um lançamento de -20. O intervalo seguinte
    # fecha, então este não tem par; como é o primeiro da conta, é o começo do histórico.
    out = _conferir(_serie(100.0, 50.0, 50.0), _transactions(_txn("t1", BANK, "2026-06-02", -30.0)))
    r = out.row(0, named=True)
    assert (r["variacao_saldo"], r["soma_lancamentos"], r["diferenca"]) == (-50.0, -30.0, -20.0)
    assert out["estado"].to_list() == [HISTORICO_INCOMPLETO, FECHA]


def test_intervalo_com_lancamento_sobrando():
    # O saldo caiu 30 e os lançamentos somam -50: sobra um lançamento de -20.
    out = _conferir(_serie(100.0, 70.0, 70.0), _transactions(_txn("t1", BANK, "2026-06-02", -30.0),
                                                             _txn("t2", BANK, "2026-06-02", -20.0)))
    r = out.row(0, named=True)
    assert (r["soma_lancamentos"], r["n_lancamentos"], r["diferenca"]) == (-50.0, 2, 20.0)
    assert out["estado"].to_list() == [HISTORICO_INCOMPLETO, FECHA]


def test_lancamento_na_borda_do_updated_at():
    # Um lançamento cujo instante é o updatedAt de um snapshot entra no intervalo que termina nele
    # (de < instante <= ate) e não no seguinte.
    accounts = _accounts(_snap(BANK, "2026-06-01T00:00:00.000Z", 100.0),
                         _snap(BANK, "2026-06-02T00:00:00.000Z", 90.0),
                         _snap(BANK, "2026-06-03T00:00:00.000Z", 90.0))
    out = _conferir(accounts, _transactions(_txn("t1", BANK, "2026-06-02", -10.0)),
                    _raw(("t1", "2026-06-02T00:00:00.000Z", "2026-06-02")))
    assert out.select("n_lancamentos", "soma_lancamentos", "estado").rows() == [
        (1, -10.0, FECHA), (0, 0.0, FECHA)]


def test_a_hora_vem_de_raw():
    # cleansed guarda só o dia: o lançamento de 02/06 às 21h é depois do sync das 19h e vai para o
    # intervalo seguinte. Sem hora em raw, o dia entra como a meia-noite UTC (antes do sync).
    accounts = _serie(100.0, 100.0, 90.0)
    tarde = _transactions(_txn("t1", BANK, "2026-06-02", -10.0))
    com_hora = _conferir(accounts, tarde, _raw(("t1", "2026-06-02T21:00:00.000Z", "2026-06-03")))
    assert com_hora.select("n_lancamentos", "diferenca").rows() == [(0, 0.0), (1, 0.0)]
    sem_hora = _conferir(accounts, tarde)
    assert sem_hora.select("n_lancamentos", "diferenca").rows() == [(1, 10.0), (0, -10.0)]


def test_a_hora_e_a_da_coleta_mais_nova():
    accounts = _serie(100.0, 100.0, 90.0)
    raw = _raw(("t1", "2026-06-03T10:00:00.000Z", "2026-06-04"), ("t1", "2026-06-02T10:00:00.000Z", "2026-06-02"))
    out = _conferir(accounts, _transactions(_txn("t1", BANK, "2026-06-02", -10.0)), raw)
    assert out["n_lancamentos"].to_list() == [0, 1]


def test_lancamento_do_dia_do_snapshot_anterior_fica_fora():
    # Um lançamento do dia de `de`, à meia-noite UTC, é anterior ao sync das 19h: não é do intervalo.
    out = _conferir(_accounts(_snap(BANK, DE, 100.0), _snap(BANK, ATE, 100.0)),
                    _transactions(_txn("t1", BANK, "2026-06-01", -10.0)))
    assert out.select("n_lancamentos", "diferenca").rows() == [(0, 0.0)]


def test_conta_com_um_so_snapshot_nao_tem_intervalo():
    # Coletas repetidas com o mesmo updatedAt são um snapshot só.
    accounts = _accounts(_snap(BANK, DE, 100.0, ingested_at="2026-06-02"),
                         _snap(BANK, DE, 100.0, ingested_at="2026-06-03"))
    out = _conferir(accounts, _transactions(_txn("t1", BANK, "2026-06-02", -10.0)))
    assert out.is_empty()
    assert "estado" in out.columns


def test_updated_at_repetido_vale_o_saldo_da_coleta_mais_nova():
    # Duas coletas com o mesmo updatedAt são um snapshot: o saldo é o da mais nova, e coletado_em é o dia
    # da primeira (a segunda não trouxe snapshot novo).
    accounts = _accounts(_snap(BANK, DE, 100.0), _snap(BANK, ATE, 80.0, ingested_at="2026-06-02"),
                         _snap(BANK, ATE, 70.0, ingested_at="2026-06-03"))
    r = _conferir(accounts, _transactions(_txn("t1", BANK, "2026-06-02", -30.0))).row(0, named=True)
    assert (r["saldo_ate"], r["diferenca"], r["coletado_em"]) == (70.0, 0.0, date(2026, 6, 2))


def test_conta_credit_fica_fora():
    accounts = _accounts(_snap(CARTAO, DE, 500.0, "CREDIT"), _snap(CARTAO, ATE, 800.0, "CREDIT"),
                         _snap(BANK, DE, 100.0), _snap(BANK, ATE, 100.0))
    out = _conferir(accounts, _transactions(_txn("c1", CARTAO, "2026-06-02", 300.0)))
    assert out["account_id"].to_list() == [BANK]


def test_lancamento_de_outra_conta_nao_entra():
    accounts = _accounts(_snap(BANK, DE, 100.0), _snap(BANK, ATE, 100.0),
                         _snap(OUTRA, DE, 10.0), _snap(OUTRA, ATE, 5.0))
    out = _conferir(accounts, _transactions(_txn("t1", OUTRA, "2026-06-02", -5.0)))
    assert out.sort("account_id").select("account_id", "n_lancamentos", "diferenca").rows() == [
        (BANK, 0, 0.0), (OUTRA, 1, 0.0)]


def test_diferenca_acumulada_soma_os_intervalos_em_ordem():
    # Snapshots fora de ordem (as partições não chegam ordenadas); lançamentos fora de toda janela
    # (antes do primeiro snapshot, depois do último) não entram em intervalo nenhum.
    accounts = _accounts(_snap(BANK, "2026-06-03T19:00:00.000Z", 75.0),
                         _snap(BANK, DE, 100.0), _snap(BANK, ATE, 90.0))
    txns = _transactions(_txn("antes", BANK, "2026-05-30", -1.0), _txn("t1", BANK, "2026-06-02", -5.0),
                         _txn("t2", BANK, "2026-06-03", -5.0), _txn("depois", BANK, "2026-06-05", -1.0))
    out = _conferir(accounts, txns)
    assert out.select("diferenca", "diferenca_acumulada").rows() == [(-5.0, -5.0), (-10.0, -15.0)]


def test_diferenca_arredonda_ao_centavo():
    r = _conferir(_accounts(_snap(BANK, DE, 0.3), _snap(BANK, ATE, 0.1)),
                  _transactions(_txn("t1", BANK, "2026-06-02", -0.1),
                                _txn("t2", BANK, "2026-06-02", -0.1))).row(0, named=True)
    assert r["diferenca"] == 0.0
    assert r["variacao_saldo"] == -0.2


@pytest.mark.parametrize("delta,estado", [(0.0, FECHA), (TOLERANCIA, FECHA), (-TOLERANCIA, FECHA),
                                          (TOLERANCIA + 0.01, EM_ABERTO), (-TOLERANCIA - 0.01, EM_ABERTO)])
def test_fecha_dentro_da_tolerancia(delta, estado):
    out = _conferir(_accounts(_snap(BANK, DE, 100.0), _snap(BANK, ATE, 100.0 + delta)), _transactions())
    assert out["estado"].to_list() == [estado]


# --- os estados: o par de horário e o intervalo em aberto -----------------------------------------

def _estados(*diferencas):
    # Uma série cujos intervalos têm exatamente estas diferenças (sem lançamentos: diferença = variação).
    saldos = [100.0]
    for d in diferencas:
        saldos.append(round(saldos[-1] + d, 2))
    return _conferir(_serie(*saldos), _transactions())["estado"].to_list()


def test_par_de_horario_fecha_com_vizinho():
    # O banco moveu o saldo uma coleta antes da data do lançamento: -8,70 num intervalo, +8,70 no seguinte.
    assert _estados(0.0, -8.70, 8.72, 0.0) == [FECHA, FECHA_COM_VIZINHO, FECHA_COM_VIZINHO, FECHA]


def test_cada_intervalo_entra_em_um_par_so():
    assert _estados(-10.0, 10.0, -10.0, 0.0) == [FECHA_COM_VIZINHO, FECHA_COM_VIZINHO, NAO_FECHA, FECHA]


def test_vizinho_que_fecha_sozinho_nao_forma_par():
    assert _estados(0.0, -1.5, 0.9, 0.0) == [FECHA, NAO_FECHA, FECHA, FECHA]


def test_vizinhos_que_nao_se_anulam_nao_fecham():
    assert _estados(0.0, -20.0, -15.0, 0.0) == [FECHA, NAO_FECHA, NAO_FECHA, FECHA]


def test_o_mais_novo_que_nao_fecha_sozinho_fica_em_aberto():
    # Ainda não há vizinho seguinte; se o anterior o completa, o par já fecha.
    assert _estados(0.0, -8.70) == [FECHA, EM_ABERTO]
    assert _estados(0.0, -8.70, 8.70) == [FECHA, FECHA_COM_VIZINHO, FECHA_COM_VIZINHO]
    assert _estados(0.0, -20.0, -8.70) == [FECHA, NAO_FECHA, EM_ABERTO]


def test_o_mais_velho_que_nao_fecha_sozinho_nem_com_o_seguinte_e_o_comeco_do_historico():
    # df-y26v: o vizinho anterior é de antes da primeira coleta de saldo (o seguro que o banco tirou antes).
    assert _estados(9.93, 0.0, 0.0) == [HISTORICO_INCOMPLETO, FECHA, FECHA]
    assert _estados(-20.0, -8.70) == [HISTORICO_INCOMPLETO, EM_ABERTO]
    # Se o seguinte o completa, é um par como qualquer outro; e um intervalo só é o mais novo: em_aberto.
    assert _estados(-9.90, 9.90, 0.0) == [FECHA_COM_VIZINHO, FECHA_COM_VIZINHO, FECHA]
    assert _estados(9.93) == [EM_ABERTO]


def test_estados_sao_os_cinco():
    assert set(ESTADOS) == {FECHA, FECHA_COM_VIZINHO, EM_ABERTO, HISTORICO_INCOMPLETO, NAO_FECHA}


# --- o aviso: o terceiro intervalo mais novo, na rodada que trouxe snapshot novo -------------------

F = _delta_util.exec_functions({"__file__": str(_delta_util.FUNCTIONS)})


def _achados(accounts, txns, today, raw=None):
    # (contagem, ates) do aviso sobre a conferência destes dados, numa rodada do dia `today`.
    conf = _conferir(accounts, txns, raw).rename({"account_id": "account_uuid"})
    return F["_offenders"](conf, F["new_balance_break_rule"](today=today)[2], ["ate"])


JUN = {d: date(2026, 6, d) for d in range(1, 7)}
# Um snapshot de 31/05 com o mesmo saldo antes de cada série: a quebra não é o primeiro intervalo da conta
# (o primeiro é historico_incompleto, nunca aviso).
ANTES = _accounts(_snap(BANK, "2026-05-31T19:00:00.000Z", 100.0))
QUEBRA_ANTIGA = pl.concat([ANTES, _serie(100.0, 80.0, 80.0, 80.0)])   # o intervalo de 01/06 a 02/06 não fecha (-20)


def test_aviso_sai_para_o_terceiro_mais_novo_na_rodada_da_coleta():
    assert _achados(QUEBRA_ANTIGA, _transactions(), JUN[4]) == (1, ["2026-06-02 19:00:00+00:00"])


def test_segunda_rodada_sem_snapshot_novo_nao_repete_o_aviso():
    assert _achados(QUEBRA_ANTIGA, _transactions(), JUN[5]) == (0, [])
    # Uma coleta de 05/06 que só repete o updatedAt de 04/06 não é snapshot novo.
    repete = pl.concat([QUEBRA_ANTIGA, _accounts(_snap(BANK, "2026-06-04T19:00:00.000Z", 80.0,
                                                       ingested_at="2026-06-05"))])
    assert _achados(repete, _transactions(), JUN[5]) == (0, [])


def test_o_primeiro_intervalo_da_conta_nunca_avisa():
    # Sem o snapshot de 31/05, a quebra de 01/06 é o começo do histórico, também como 3º mais novo.
    assert _achados(_serie(100.0, 80.0, 80.0, 80.0), _transactions(), JUN[4]) == (0, [])


def test_quebra_fora_do_terceiro_lugar_nao_avisa():
    assert _achados(_serie(100.0, 80.0, 80.0), _transactions(), JUN[3]) == (0, [])          # 2º mais novo
    assert _achados(_serie(100.0, 80.0, 80.0, 80.0, 80.0), _transactions(), JUN[5]) == (0, [])   # 4º


def test_lancamento_que_chega_uma_coleta_depois_nao_avisa():
    # O banco posta depois do fim de semana: na rodada de 03/06 o lançamento de 02/06 ainda não veio
    # (o intervalo é o 2º mais novo, sem aviso); na de 04/06 ele chegou e o intervalo fecha.
    serie = pl.concat([ANTES, _serie(100.0, 70.0, 70.0, 70.0)])
    assert _achados(serie.head(4), _transactions(), JUN[3]) == (0, [])
    assert _achados(serie, _transactions(_txn("t1", BANK, "2026-06-02", -30.0)), JUN[4]) == (0, [])
    # Se não chega nunca, a rodada de 04/06 avisa.
    assert _achados(serie, _transactions(), JUN[4]) == (1, ["2026-06-02 19:00:00+00:00"])


def test_aviso_por_conta():
    # As duas contas têm a quebra no 3º intervalo mais novo; só avisa a conta cujo snapshot mais novo
    # chegou na rodada.
    outra = _accounts(*[_snap(OUTRA, f"2026-{d}T19:00:00.000Z", s)
                        for d, s in [("05-30", 10.0), ("05-31", 10.0), ("06-01", 5.0), ("06-02", 5.0),
                                     ("06-03", 5.0)]])
    duas = pl.concat([QUEBRA_ANTIGA, outra])
    assert _achados(duas, _transactions(), JUN[4]) == (1, ["2026-06-02 19:00:00+00:00"])
    assert _achados(duas, _transactions(), JUN[3]) == (1, ["2026-06-01 19:00:00+00:00"])


# --- a task -------------------------------------------------------------------------------------

def _seed_partition(tmp_path, entity, day, frame):
    part = tmp_path / "raw" / entity / f"ingestion_date={day}"
    part.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(part / "data.parquet")


TXNS = [_txn("t1", BANK, "2026-06-02", -30.0), _txn("t2", BANK, "2026-06-03", -10.0)]


def _seed(tmp_path, *saldos):
    # Uma coleta da conta BANK por dia a partir de 01/06 (100 e 50 nas duas primeiras, depois `saldos`) e
    # duas do cartão. O intervalo de 01/06 a 02/06 não fecha: falta -20. Uma coleta de 31/05 com o mesmo
    # saldo vem antes: a quebra não é o primeiro intervalo da conta.
    _seed_partition(tmp_path, "accounts", "2026-05-31", _accounts(_snap(BANK, "2026-05-31T19:00:00.000Z", 100.0)))
    for d, s in enumerate((100.0, 50.0, *saldos), 1):
        rows = [_snap(BANK, f"2026-06-{d:02d}T19:00:00.000Z", s)]
        if d <= 2:
            rows.append(_snap(CARTAO, f"2026-06-{d:02d}T19:00:00.000Z", 500.0 + 100 * d, "CREDIT"))
        _seed_partition(tmp_path, "accounts", f"2026-06-{d:02d}", _accounts(*rows))
    _seed_partition(tmp_path, "transactions", "2026-06-03",
                    _raw(("t1", "2026-06-02T03:00:00.000Z", "2026-06-03"),
                         ("t2", "2026-06-03T03:00:00.000Z", "2026-06-03")))
    _delta_util.seed(tmp_path, "cleansed", "transactions", _transactions(*TXNS))
    _delta_util.seed(tmp_path, "curated", "dim_account", pl.DataFrame(
        {"pluggy_account_id": [BANK, CARTAO], "account_uuid": ["banco-conta-0001", "banco-cartao-0002"]}))


def _run(tmp_path, day):
    return _delta_util.run_script("cleansed2curated", "cleansed2curated_conferencia_saldo",
                                  ["--source", "raw", "--target", "curated"], tmp_path,
                                  overrides={"run_day": lambda: JUN[day]})


def _findings(tmp_path):
    return json.loads((tmp_path / "validation" / "curated.conferencia_saldo.json").read_text())["findings"]


def test_task_grava_por_account_uuid_e_avisa_a_quebra(tmp_path):
    _seed(tmp_path, 40.0, 40.0)
    _run(tmp_path, 4)
    out = _delta_util.read(tmp_path, "curated", "conferencia_saldo").sort("ate")
    assert "account_id" not in out.columns
    assert out.select("account_uuid", "coletado_em", "diferenca", "diferenca_acumulada", "estado").rows() == [
        ("banco-conta-0001", JUN[1], 0.0, 0.0, FECHA), ("banco-conta-0001", JUN[2], -20.0, -20.0, NAO_FECHA), ("banco-conta-0001", JUN[3], 0.0, -20.0, FECHA),
        ("banco-conta-0001", JUN[4], 0.0, -20.0, FECHA)]
    (finding,) = _findings(tmp_path)
    assert finding["rule"] == "the third-newest interval of an account does not close"
    assert (finding["count"], finding["keys"]) == (1, ["2026-06-02 19:00:00+00:00"])


def test_task_sem_snapshot_novo_nao_avisa(tmp_path):
    _seed(tmp_path, 40.0, 40.0)
    _run(tmp_path, 5)
    assert _findings(tmp_path) == []


def test_task_quebra_antiga_nao_avisa(tmp_path):
    _seed(tmp_path, 40.0, 40.0, 40.0)
    _run(tmp_path, 5)
    assert _findings(tmp_path) == []


def test_task_imprime_contagens_e_os_mais_novos_sem_valores(tmp_path, capsys):
    # O mais novo não fecha sozinho e ainda não tem vizinho: em_aberto. O log não leva valor nenhum.
    _seed(tmp_path, 40.0, 40.0, 30.0)
    _run(tmp_path, 5)
    printed = capsys.readouterr().out
    assert ("conferencia_saldo: 5 intervalos (tolerância 1.00): fecha 3, fecha_com_vizinho 0, em_aberto 1, "
            "historico_incompleto 0, nao_fecha 1") in printed
    assert "entre os 3 mais novos, 2026-06-04 19:00 -> 2026-06-05 19:00 não fecha (em_aberto)" in printed
    # O intervalo de 01/06 não está entre os 3 mais novos; nenhum valor sai no log.
    assert "2026-06-01 19:00 ->" not in printed
    assert not any(v in printed for v in ("-20", "-10", "20.00", "10.00"))
