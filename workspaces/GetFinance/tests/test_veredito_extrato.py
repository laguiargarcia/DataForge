"""Veredito do extrato (df-z4fh.3): cada lançamento de cleansed é casado com uma linha do extrato do banco, e
cada suspeito ganha um veredito (no extrato, fora dele, ou o extrato não cobre o seu dia). Só lê e mede.

A função pura (scripts/report/veredito_extrato.py) e a task que grava curated.veredito_suspeitos,
curated.extrato_sem_lancamento e curated.acerto_por_motivo. Dados falsos: nenhum id, nome ou valor vem do
dado real.
"""
import json
import sys
from datetime import date

import polars as pl
import pytest

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from extrato import COLUNAS as COLUNAS_EXTRATO
from extrato import COLUNAS_SALDOS
from lancamentos_suspeitos import MOTIVOS
from veredito_extrato import (COLUNAS_ACERTO, COLUNAS_CASAMENTO, COLUNAS_SEM_LANCAMENTO, FORA_DO_EXTRATO,
                              NO_EXTRATO, SEM_EXTRATO, TOLERANCIA_DIAS, acerto_por_motivo, casar,
                              sem_lancamento, vereditos)

BANK, CARTAO = "p-bank-1", "p-card-1"
COLETA = "2026-07-01T06:00:00.000Z"
SUMIU, PENDENTE, APLICACAO = "sumiu_da_coleta", "pendente_que_sumiu", "aplicacao_automatica"

_TXN = {"id": pl.String, "accountId": pl.String, "date": pl.Date, "amount": pl.Float64, "status": pl.String}
_RAW = {"id": pl.String, "date": pl.String, "ingested_at": pl.String}
_SUSP = {"transaction_id": pl.String, "account_uuid": pl.String, "date": pl.Date, "amount": pl.Float64,
         "motivo": pl.String}


def _d(texto):
    return date.fromisoformat(texto)


def _t(id_, quando, amount=-10.0, account=BANK, status="POSTED"):
    # Um lançamento: `quando` é o instante UTC de raw; cleansed guarda o dia UTC dele.
    return id_, account, quando, amount, status


def _transactions(*ts):
    return pl.DataFrame([(i, c, _d(q[:10]), a, st) for i, c, q, a, st in ts], schema=_TXN, orient="row")


def _raw(*ts):
    return pl.DataFrame([(t[0], t[2], COLETA) for t in ts], schema=_RAW, orient="row")


def _extrato(*linhas):
    # Cada linha é (dia, valor) ou (dia, valor, ordem, arquivo, conta).
    rows = []
    for linha in linhas:
        dia, valor, ordem, arquivo, conta = (*linha, 1, "a.pdf", BANK)[:5] if len(linha) == 2 else linha
        rows.append({"arquivo": arquivo, "account_id": conta, "dia": _d(dia), "ordem": ordem,
                     "descricao": "COMPRA FICTICIA", "valor": valor, "saldo_do_dia": None})
    return pl.DataFrame(rows, schema=COLUNAS_EXTRATO)


def _saldos(*dias, arquivo="a.pdf", conta=BANK):
    return pl.DataFrame([{"account_id": conta, "dia": _d(d), "arquivo": arquivo} for d in dias],
                        schema={k: COLUNAS_SALDOS[k] for k in ("account_id", "dia", "arquivo")})


# O extrato de junho: o saldo anterior é o de 31/05, o último o de 30/06; cobre de 01/06 a 30/06.
JUNHO = _saldos("2026-05-31", "2026-06-30")


def _casar(ts, extrato, saldos=JUNHO, tolerancia=TOLERANCIA_DIAS):
    return casar(_transactions(*ts), _raw(*ts), extrato, saldos, tolerancia)


def _veredito(casamento, id_):
    return casamento.filter(pl.col("id") == id_)["veredito"].item()


def _par(casamento, id_):
    return casamento.filter(pl.col("id") == id_).select("extrato_dia", "extrato_ordem").row(0)


def test_lancamento_que_esta_no_extrato():
    # O banco lança dois dias depois da data do Pluggy.
    out = _casar([_t("s1", "2026-06-10T15:00:00.000Z", -41.0)], _extrato(("2026-06-12", -41.0)))
    assert dict(out.schema) == COLUNAS_CASAMENTO
    assert out.row(0, named=True) == {"id": "s1", "account_id": BANK, "dia_banco": _d("2026-06-10"),
                                      "amount": -41.0, "extrato_dia": _d("2026-06-12"), "extrato_ordem": 1,
                                      "veredito": NO_EXTRATO}


def test_lancamento_que_nao_esta_no_extrato():
    out = _casar([_t("s1", "2026-06-10T15:00:00.000Z", -41.0)], _extrato(("2026-06-12", -77.0)))
    assert _veredito(out, "s1") == FORA_DO_EXTRATO


def test_valor_tem_de_bater_ao_centavo_e_com_o_sinal():
    ts = [_t("a", "2026-06-10T15:00:00.000Z", -41.01), _t("b", "2026-06-11T15:00:00.000Z", 41.0)]
    out = _casar(ts, _extrato(("2026-06-10", -41.0), ("2026-06-11", -41.0, 2, "a.pdf", BANK)))
    assert out["veredito"].to_list() == [FORA_DO_EXTRATO, FORA_DO_EXTRATO]


@pytest.mark.parametrize("dias,casa", [(TOLERANCIA_DIAS, True), (TOLERANCIA_DIAS + 1, False)])
def test_tolerancia_em_dias_para_os_dois_lados(dias, casa):
    depois = _d("2026-06-15").toordinal() + dias
    antes = _d("2026-06-15").toordinal() - dias
    ts = [_t("depois", "2026-06-15T15:00:00.000Z"), _t("antes", "2026-06-15T15:00:00.000Z", -20.0)]
    out = _casar(ts, _extrato((date.fromordinal(depois).isoformat(), -10.0),
                              (date.fromordinal(antes).isoformat(), -20.0)))
    assert out["veredito"].to_list() == [NO_EXTRATO if casa else FORA_DO_EXTRATO] * 2


def test_linha_do_extrato_sem_lancamento():
    extrato = _extrato(("2026-06-12", -41.0), ("2026-06-15", -77.0, 1, "a.pdf", BANK))
    out = _casar([_t("s1", "2026-06-12T15:00:00.000Z", -41.0)], extrato)
    sem = sem_lancamento(extrato, out)
    assert dict(sem.schema) == COLUNAS_SEM_LANCAMENTO
    assert sem.select("dia", "ordem", "valor").rows() == [(_d("2026-06-15"), 1, -77.0)]


def test_dois_lancamentos_de_mesmo_valor_no_mesmo_dia_casam_com_duas_linhas():
    ts = [_t("a", "2026-06-12T13:00:00.000Z"), _t("b", "2026-06-12T15:00:00.000Z")]
    extrato = _extrato(("2026-06-12", -10.0, 1, "a.pdf", BANK), ("2026-06-12", -10.0, 2, "a.pdf", BANK))
    out = _casar(ts, extrato)
    assert (_par(out, "a"), _par(out, "b")) == ((_d("2026-06-12"), 1), (_d("2026-06-12"), 2))
    assert sem_lancamento(extrato, out).is_empty()


def test_dois_lancamentos_iguais_e_uma_linha_so_o_menor_id_fica_com_ela():
    ts = [_t("b", "2026-06-12T13:00:00.000Z"), _t("a", "2026-06-12T15:00:00.000Z")]
    out = _casar(ts, _extrato(("2026-06-12", -10.0)))
    assert (_veredito(out, "a"), _veredito(out, "b")) == (NO_EXTRATO, FORA_DO_EXTRATO)


def test_no_empate_do_mesmo_dia_o_postado_fica_com_a_linha_antes_do_id():
    # Pela ordem do id o PENDING "a" ganharia; o extrato lista o lançado, e o PENDING igual é a duplicata.
    ts = [_t("a", "2026-06-12T13:00:00.000Z", status="PENDING"), _t("b", "2026-06-12T15:00:00.000Z")]
    out = _casar(ts, _extrato(("2026-06-12", -10.0)))
    assert (_veredito(out, "b"), _veredito(out, "a")) == (NO_EXTRATO, FORA_DO_EXTRATO)


def test_tolerancia_e_a_medida_no_extrato_real():
    # 4 dias: com 3, três compras reais de fevereiro ficam sem par; com 5, o único par a mais é um rendimento de
    # centavos que a df-7twv conta sem par (notas da df-z4fh.3). Mudar o número pede refazer essa medida.
    assert TOLERANCIA_DIAS == 4


def test_suspeito_nao_toma_o_par_de_um_lancamento_normal():
    # Casar só os suspeitos daria a linha de 12/06 ao suspeito de 10/06; o normal de 12/06 está mais perto.
    ts = [_t("normal", "2026-06-12T15:00:00.000Z", -50.0), _t("suspeito", "2026-06-10T15:00:00.000Z", -50.0)]
    out = _casar(ts, _extrato(("2026-06-12", -50.0)))
    assert (_veredito(out, "normal"), _veredito(out, "suspeito")) == (NO_EXTRATO, FORA_DO_EXTRATO)


def test_na_mesma_distancia_ganha_o_lancamento_mais_cedo_e_a_linha_mais_cedo():
    ts = [_t("tarde", "2026-06-12T15:00:00.000Z"), _t("cedo", "2026-06-10T15:00:00.000Z")]
    out = _casar(ts, _extrato(("2026-06-11", -10.0)))
    assert (_veredito(out, "cedo"), _veredito(out, "tarde")) == (NO_EXTRATO, FORA_DO_EXTRATO)
    out = _casar([_t("x", "2026-06-11T15:00:00.000Z")],
                 _extrato(("2026-06-12", -10.0), ("2026-06-10", -10.0, 1, "a.pdf", BANK)))
    assert _par(out, "x") == (_d("2026-06-10"), 1)


def test_o_dia_e_o_do_fuso_do_banco():
    # 11/06 às 01:30 UTC é 10/06 às 22:30 em Brasília: casa com a linha de 10/06, sem tolerância.
    ts = [_t("t1", "2026-06-11T01:30:00.000Z")]
    out = _casar(ts, _extrato(("2026-06-10", -10.0), ("2026-06-11", -10.0, 1, "a.pdf", BANK)), tolerancia=0)
    assert out.select("dia_banco", "extrato_dia").row(0) == (_d("2026-06-10"), _d("2026-06-10"))


def test_id_sem_hora_em_raw_fica_com_o_dia_de_cleansed():
    ts = [_t("t1", "2026-06-10T15:00:00.000Z")]
    out = casar(_transactions(*ts), _raw(), _extrato(("2026-06-10", -10.0)), JUNHO, 0)
    assert out.select("dia_banco", "veredito").row(0) == (_d("2026-06-10"), NO_EXTRATO)


@pytest.mark.parametrize("quando,veredito", [
    ("2026-07-10T15:00:00.000Z", SEM_EXTRATO),   # depois do período
    ("2026-05-20T15:00:00.000Z", SEM_EXTRATO),   # antes
    ("2026-06-27T15:00:00.000Z", SEM_EXTRATO),   # o par poderia estar em 01/07, que o extrato não cobre
    ("2026-06-26T15:00:00.000Z", FORA_DO_EXTRATO),
    ("2026-06-04T15:00:00.000Z", SEM_EXTRATO),   # o par poderia estar em 31/05: o saldo anterior não lista
    ("2026-06-05T15:00:00.000Z", FORA_DO_EXTRATO),
])
def test_lancamento_fora_do_periodo_ou_perto_da_borda_fica_sem_veredito(quando, veredito):
    out = _casar([_t("t1", quando)], _extrato(("2026-06-15", -99.0)))
    assert _veredito(out, "t1") == veredito


def test_perto_da_borda_mas_com_par_esta_no_extrato():
    out = _casar([_t("t1", "2026-06-29T15:00:00.000Z")], _extrato(("2026-06-30", -10.0)))
    assert _veredito(out, "t1") == NO_EXTRATO


def test_conta_sem_extrato_como_o_cartao_fica_sem_veredito():
    out = _casar([_t("c1", "2026-06-15T15:00:00.000Z", account=CARTAO)], _extrato(("2026-06-15", -10.0)))
    assert _par(out, "c1") == (None, None)
    assert _veredito(out, "c1") == SEM_EXTRATO


def test_buraco_entre_arquivos_nao_e_coberto_e_arquivos_que_se_tocam_viram_um_periodo():
    saldos = pl.concat([JUNHO, _saldos("2026-07-31", "2026-08-31", arquivo="b.pdf"),
                        _saldos("2026-06-30", "2026-07-10", arquivo="c.pdf")])
    ts = [_t("julho_fim", "2026-07-20T15:00:00.000Z"), _t("agosto", "2026-08-15T15:00:00.000Z"),
          _t("virada", "2026-06-29T15:00:00.000Z")]
    out = _casar(ts, _extrato(("2026-06-15", -99.0)), saldos)
    assert [_veredito(out, i) for i in ("julho_fim", "agosto", "virada")] == [SEM_EXTRATO, FORA_DO_EXTRATO,
                                                                              FORA_DO_EXTRATO]


def test_arquivo_sem_saldo_cobre_do_primeiro_ao_ultimo_lancamento():
    extrato = _extrato(("2026-06-01", -99.0), ("2026-06-30", -98.0))
    out = _casar([_t("t1", "2026-06-15T15:00:00.000Z")], extrato, _saldos())
    assert _veredito(out, "t1") == FORA_DO_EXTRATO


def test_contas_nao_se_misturam():
    cobre_o_cartao = _saldos("2026-05-31", "2026-06-30", conta=CARTAO)
    out = _casar([_t("t1", "2026-06-15T15:00:00.000Z", account=CARTAO)], _extrato(("2026-06-15", -10.0)),
                 cobre_o_cartao)
    assert _veredito(out, "t1") == FORA_DO_EXTRATO


def test_sem_extrato_tudo_fica_sem_veredito_e_as_tabelas_saem_tipadas():
    extrato = pl.DataFrame(schema=COLUNAS_EXTRATO)
    out = _casar([_t("t1", "2026-06-15T15:00:00.000Z")], extrato, pl.DataFrame(schema=COLUNAS_SALDOS))
    assert out["veredito"].to_list() == [SEM_EXTRATO]
    assert dict(sem_lancamento(extrato, out).schema) == COLUNAS_SEM_LANCAMENTO


# --- veredito dos suspeitos e acerto por motivo ----------------------------------------------------

def _suspeitos(*rows):
    return pl.DataFrame([(i, "banco-conta-0001", _d("2026-06-10"), -10.0, m) for i, m in rows], schema=_SUSP,
                        orient="row")


def test_veredito_de_cada_suspeito_por_motivo():
    ts = [_t("no", "2026-06-10T15:00:00.000Z"), _t("fora", "2026-06-10T15:00:00.000Z", -20.0),
          _t("normal", "2026-06-11T15:00:00.000Z", -30.0)]
    casamento = _casar(ts, _extrato(("2026-06-11", -10.0), ("2026-06-11", -30.0, 2, "a.pdf", BANK)))
    out = vereditos(casamento, _suspeitos(("no", SUMIU), ("fora", SUMIU), ("fora", PENDENTE), ("sumido", SUMIU)))
    assert out.select("transaction_id", "motivo", "veredito", "extrato_dia").rows() == [
        ("no", SUMIU, NO_EXTRATO, _d("2026-06-11")), ("fora", SUMIU, FORA_DO_EXTRATO, None),
        ("fora", PENDENTE, FORA_DO_EXTRATO, None), ("sumido", SUMIU, SEM_EXTRATO, None)]
    assert out["account_uuid"].unique().to_list() == ["banco-conta-0001"]


def test_acerto_por_motivo_conta_cada_veredito_na_ordem_dos_motivos():
    v = pl.DataFrame({"motivo": [SUMIU, SUMIU, SUMIU, APLICACAO, "motivo_novo"],
                      "veredito": [NO_EXTRATO, FORA_DO_EXTRATO, SEM_EXTRATO, FORA_DO_EXTRATO, NO_EXTRATO]})
    out = acerto_por_motivo(v, [SUMIU, PENDENTE, APLICACAO])
    assert dict(out.schema) == COLUNAS_ACERTO
    assert out.rows() == [(SUMIU, 3, 1, 1), (PENDENTE, 0, 0, 0), (APLICACAO, 1, 0, 1), ("motivo_novo", 1, 1, 0)]


# --- a task -------------------------------------------------------------------------------------

UUID = "banco-conta-0001"


def _seed(tmp_path, com_extrato=True):
    ts = [_t("s1", "2026-06-10T15:00:00.000Z", -41.0), _t("a1", "2026-06-12T15:00:00.000Z", 418.0),
          _t("ok", "2026-06-14T15:00:00.000Z", -12.0)]
    part = tmp_path / "raw" / "transactions" / "ingestion_date=2026-07-01"
    part.mkdir(parents=True)
    _raw(*ts).with_columns(accountId=pl.lit(BANK), amount=pl.lit("1.0")).write_parquet(part / "data.parquet")
    _delta_util.seed(tmp_path, "cleansed", "transactions", _transactions(*ts))
    _delta_util.seed(tmp_path, "curated", "dim_account",
                     pl.DataFrame({"pluggy_account_id": [BANK], "account_uuid": [UUID]}))
    _delta_util.seed(tmp_path, "curated", "lancamentos_suspeitos",
                     _suspeitos(("s1", SUMIU), ("a1", APLICACAO)).with_columns(description=pl.lit("X")))
    if com_extrato:
        linhas = _extrato(("2026-06-12", -41.0), ("2026-06-14", -12.0), ("2026-06-20", -77.0))
        _delta_util.seed(tmp_path, "curated", "extrato",
                         linhas.rename({"account_id": "account_uuid"}).with_columns(account_uuid=pl.lit(UUID)))
        _delta_util.seed(tmp_path, "curated", "extrato_saldos",
                         JUNHO.rename({"account_id": "account_uuid"}).with_columns(account_uuid=pl.lit(UUID)))


def _run(tmp_path):
    return _delta_util.run_script("cleansed2curated", "cleansed2curated_veredito_suspeitos",
                                  ["--source", "raw", "--target", "curated"], tmp_path)


def test_task_grava_as_tres_tabelas_por_account_uuid_sem_achado(tmp_path):
    _seed(tmp_path)
    _run(tmp_path)
    vered = _delta_util.read(tmp_path, "curated", "veredito_suspeitos").sort("transaction_id")
    assert vered.select("transaction_id", "account_uuid", "motivo", "veredito").rows() == [
        ("a1", UUID, APLICACAO, FORA_DO_EXTRATO), ("s1", UUID, SUMIU, NO_EXTRATO)]
    sem = _delta_util.read(tmp_path, "curated", "extrato_sem_lancamento")
    assert sem.select("account_uuid", "dia", "valor").rows() == [(UUID, _d("2026-06-20"), -77.0)]
    acerto = _delta_util.read(tmp_path, "curated", "acerto_por_motivo")
    assert acerto.rows() == [(SUMIU, 1, 1, 0), (PENDENTE, 0, 0, 0), (APLICACAO, 1, 0, 1),
                             ("uma_coleta_so", 0, 0, 0)]
    for tabela in ("veredito_suspeitos", "extrato_sem_lancamento", "acerto_por_motivo"):
        findings = json.loads((tmp_path / "validation" / f"curated.{tabela}.json").read_text())
        assert findings["findings"] == []


def test_task_sem_tabelas_de_extrato_da_sem_veredito_e_nao_falha(tmp_path):
    _seed(tmp_path, com_extrato=False)
    _run(tmp_path)
    vered = _delta_util.read(tmp_path, "curated", "veredito_suspeitos")
    assert vered["veredito"].to_list() == [SEM_EXTRATO, SEM_EXTRATO]
    assert _delta_util.read(tmp_path, "curated", "extrato_sem_lancamento").is_empty()


def test_task_imprime_contagens_sem_ids_nem_valores(tmp_path, capsys):
    _seed(tmp_path)
    _run(tmp_path)
    printed = capsys.readouterr().out
    assert (f"veredito_suspeitos: 2 lançamentos suspeitos (tolerância {TOLERANCIA_DIAS} dias): no_extrato 1, "
            "fora_do_extrato 1, sem_extrato_no_periodo 0") in printed
    assert ("veredito_suspeitos: extrato: 1 linha(s) sem lançamento; 1 lançamento(s) do período coberto sem "
            "linha") in printed
    assert f"veredito_suspeitos: {SUMIU}: 1 suspeito(s), no_extrato 1, fora_do_extrato 0" in printed
    assert not any(v in printed for v in ("s1", "a1", "41", "418", "77", UUID))


def test_os_motivos_da_tabela_sao_os_de_lancamentos_suspeitos():
    assert [nome for nome, _ in MOTIVOS] == [SUMIU, PENDENTE, APLICACAO, "uma_coleta_so"]
