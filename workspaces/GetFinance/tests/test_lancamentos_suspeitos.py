"""Lançamentos suspeitos (df-z4fh.2): cada lançamento de cleansed.transactions que um motivo marca, com o
intervalo da conferência pelo saldo em que cai. Só marca: nada muda no dado.

A função pura (scripts/report/lancamentos_suspeitos.py) e a task que grava curated.lancamentos_suspeitos.
Dados falsos: nenhum id, nome ou valor vem do dado real.
"""
import json
import sys
from datetime import date, datetime, timezone

import polars as pl
import pytest

from tests import _delta_util

sys.path.insert(0, str(_delta_util.SCRIPTS / "report"))

from conferencia_saldo import FECHA, NAO_FECHA
from lancamentos_suspeitos import COLUNAS, MOTIVOS, lancamentos_suspeitos

BANK, OUTRA = "p-bank-1", "p-bank-2"
SUMIU, PENDENTE, APLICACAO, UMA_SO = ("sumiu_da_coleta", "pendente_que_sumiu", "aplicacao_automatica",
                                      "uma_coleta_so")
D1, D2, D3 = "2026-06-03", "2026-06-04", "2026-06-05"   # três coletas, a mais nova é D3

_TXN_SCHEMA = {"id": pl.Utf8, "accountId": pl.Utf8, "date": pl.Date, "amount": pl.Float64,
               "description": pl.Utf8, "status": pl.Utf8}
_RAW_SCHEMA = {"id": pl.Utf8, "accountId": pl.Utf8, "date": pl.Utf8, "ingested_at": pl.Utf8}
_CONF_SCHEMA = {"account_id": pl.Utf8, "de": pl.Datetime("us", "UTC"), "ate": pl.Datetime("us", "UTC"),
                "estado": pl.Utf8}


def _utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def _txn(id_, day, amount=-10.0, description="COMPRA LOJA FICTICIA", status="POSTED", account=BANK):  # noqa: PLR0913 -- one keyword per column the tests vary
    return {"id": id_, "accountId": account, "date": date.fromisoformat(day), "amount": amount,
            "description": description, "status": status}


def _transactions(*rows):
    return pl.DataFrame(list(rows), schema=_TXN_SCHEMA)


def _raw(*coletas):
    # Cada coleta é (dia da coleta, [(id, date com hora)], conta); raw/transactions é todo texto.
    rows = [(id_, conta, quando, dia) for dia, itens, conta in coletas for id_, quando in itens]
    return pl.DataFrame(rows, schema=_RAW_SCHEMA, orient="row")


def _coleta(dia, *itens, conta=BANK):
    return dia, list(itens), conta


def _conferencia(*rows):
    return pl.DataFrame([(c, _utc(de), _utc(ate), e) for c, de, ate, e in rows], schema=_CONF_SCHEMA,
                        orient="row")


# Um lançamento que toda coleta traz: âncora da janela da coleta mais nova (01/06 a 05/06).
FIXO = ("fixo", "2026-06-01T12:00:00.000Z")
FIM = ("fim", "2026-06-05T12:00:00.000Z")


def _suspeitos(transactions, raw, conferencia=None):
    return lancamentos_suspeitos(transactions, raw, _conferencia() if conferencia is None else conferencia)


def _motivos(out):
    return sorted(out.select("transaction_id", "motivo").rows())


def _base(*extra):
    return [_txn("fixo", "2026-06-01"), _txn("fim", "2026-06-05"), *extra]


def test_lancamento_sem_motivo_nao_entra():
    # Está em todas as coletas, inclusive na mais nova, é POSTED e não é aplicação automática.
    raw = _raw(_coleta(D1, FIXO, ("t1", "2026-06-02T10:00:00.000Z")),
               _coleta(D2, FIXO, ("t1", "2026-06-02T10:00:00.000Z")),
               _coleta(D3, FIXO, FIM, ("t1", "2026-06-02T10:00:00.000Z")))
    out = _suspeitos(_transactions(*_base(_txn("t1", "2026-06-02"))), raw)
    assert out.is_empty()
    assert dict(out.schema) == COLUNAS


def test_sumiu_da_coleta():
    # t1 veio em duas coletas e a mais nova não o traz, embora seu dia esteja na janela dela.
    raw = _raw(_coleta(D1, FIXO, ("t1", "2026-06-02T10:00:00.000Z")),
               _coleta(D2, FIXO, ("t1", "2026-06-02T10:00:00.000Z")),
               _coleta(D3, FIXO, FIM))
    out = _suspeitos(_transactions(*_base(_txn("t1", "2026-06-02", -41.0))), raw)
    assert out.drop("intervalo_de", "intervalo_ate", "intervalo_estado").to_dicts() == [{
        "transaction_id": "t1", "account_id": BANK, "date": date(2026, 6, 2), "amount": -41.0,
        "description": "COMPRA LOJA FICTICIA", "status": "POSTED", "motivo": SUMIU,
        "primeira_coleta": date(2026, 6, 3), "ultima_coleta": date(2026, 6, 4), "n_coletas": 2}]


def test_sumiu_fora_da_janela_da_coleta_mais_nova_nao_conta():
    # A coleta mais nova começa em 01/06: um lançamento de 31/05 que ela não traz saiu da janela, não
    # sumiu.
    raw = _raw(_coleta(D1, FIXO, ("velho", "2026-05-31T10:00:00.000Z")),
               _coleta(D2, FIXO, ("velho", "2026-05-31T10:00:00.000Z")),
               _coleta(D3, FIXO, FIM))
    assert _suspeitos(_transactions(*_base(_txn("velho", "2026-05-31"))), raw).is_empty()


def test_janela_comparada_por_dia():
    # A coleta mais nova começa em 01/06 às 12h; um lançamento de 01/06 às 03h que ela não traz está na
    # janela (o dia conta, não a hora).
    raw = _raw(_coleta(D1, FIXO, ("cedo", "2026-06-01T03:00:00.000Z")),
               _coleta(D2, FIXO, ("cedo", "2026-06-01T03:00:00.000Z")),
               _coleta(D3, FIXO, FIM))
    out = _suspeitos(_transactions(*_base(_txn("cedo", "2026-06-01"))), raw)
    assert _motivos(out) == [("cedo", SUMIU)]


def test_pendente_que_sumiu():
    raw = _raw(_coleta(D1, FIXO, ("p1", "2026-06-02T10:00:00.000Z"), ("p2", "2026-06-02T11:00:00.000Z")),
               _coleta(D2, FIXO, ("p1", "2026-06-02T10:00:00.000Z"), ("p2", "2026-06-02T11:00:00.000Z")),
               _coleta(D3, FIXO, FIM, ("p2", "2026-06-02T11:00:00.000Z")))
    # p1 é PENDING e sumiu; p2 é PENDING e segue na coleta mais nova.
    txns = _transactions(*_base(_txn("p1", "2026-06-02", status="PENDING"),
                                _txn("p2", "2026-06-02", status="PENDING")))
    assert _motivos(_suspeitos(txns, raw)) == [("p1", PENDENTE), ("p1", SUMIU)]


@pytest.mark.parametrize("description,marca", [
    ("Resgate RES APLIC AUT FICT", True), ("Aplicação APL APLIC AUT FICT", True),
    ("Entrada RES APLIC AUT FICT AP", True), ("Saída APL APLIC AUT FICT AP", True),
    ("REND PAGO APLIC AUT FICT", False), ("Rendimentos REND PAGO APLIC AUT FICT", False),
    ("APLICACAO CDB FICTICIA", False), ("Débito automático DA OPERADORA FICTICIA", False),
])
def test_aplicacao_automatica(description, marca):
    # Movimento interno conta <-> aplicação automática: o rendimento pago e a aplicação manual não entram.
    raw = _raw(*[_coleta(d, FIXO, ("a1", "2026-06-02T10:00:00.000Z")) for d in (D1, D2)],
               _coleta(D3, FIXO, FIM, ("a1", "2026-06-02T10:00:00.000Z")))
    out = _suspeitos(_transactions(*_base(_txn("a1", "2026-06-02", 312.0, description))), raw)
    assert _motivos(out) == ([("a1", APLICACAO)] if marca else [])


def test_uma_coleta_so():
    # u1 veio só na coleta D2; seu dia é anterior à janela da mais nova, então não "sumiu", mas esteve em
    # uma coleta só. n1 também está numa coleta só, mas é a mais nova: não é suspeito.
    raw = _raw(_coleta(D1, FIXO),
               _coleta(D2, FIXO, ("u1", "2026-05-30T10:00:00.000Z")),
               _coleta(D3, FIXO, FIM, ("n1", "2026-06-05T13:00:00.000Z")))
    txns = _transactions(*_base(_txn("u1", "2026-05-30"), _txn("n1", "2026-06-05")))
    out = _suspeitos(txns, raw)
    assert _motivos(out) == [("u1", UMA_SO)]
    assert out.select("primeira_coleta", "ultima_coleta", "n_coletas").row(0) == (date(2026, 6, 4),
                                                                                  date(2026, 6, 4), 1)


def test_um_lancamento_pode_ter_mais_de_um_motivo():
    # Uma linha de resgate automático pendente que veio numa coleta só e sumiu.
    raw = _raw(_coleta(D1, FIXO), _coleta(D2, FIXO, ("r1", "2026-06-02T10:00:00.000Z")),
               _coleta(D3, FIXO, FIM))
    txns = _transactions(*_base(_txn("r1", "2026-06-02", 50.0, "Entrada RES APLIC AUT FICT AP", "PENDING")))
    assert _motivos(_suspeitos(txns, raw)) == [("r1", APLICACAO), ("r1", PENDENTE), ("r1", SUMIU),
                                               ("r1", UMA_SO)]


def test_motivos_tem_nome_unico():
    nomes = [nome for nome, _ in MOTIVOS]
    assert nomes == [SUMIU, PENDENTE, APLICACAO, UMA_SO]


def test_coleta_mais_nova_e_janela_sao_por_conta():
    # A coleta mais nova de OUTRA é D2, e o lançamento dela está lá: não sumiu, mesmo sem vir em D3.
    raw = _raw(_coleta(D1, FIXO), _coleta(D2, FIXO), _coleta(D3, FIXO, FIM),
               _coleta(D1, ("o1", "2026-06-02T10:00:00.000Z"), conta=OUTRA),
               _coleta(D2, ("o1", "2026-06-02T10:00:00.000Z"), conta=OUTRA))
    txns = _transactions(*_base(_txn("o1", "2026-06-02", account=OUTRA)))
    assert _suspeitos(txns, raw).is_empty()


# --- o intervalo da conferência pelo saldo --------------------------------------------------------

CONF = _conferencia((BANK, "2026-06-01T19:00:00.000Z", "2026-06-02T19:00:00.000Z", NAO_FECHA),
                    (BANK, "2026-06-02T19:00:00.000Z", "2026-06-03T19:00:00.000Z", FECHA))


def _intervalo(quando, dia, conferencia=CONF):
    raw = _raw(_coleta(D1, FIXO, ("t1", quando)), _coleta(D2, FIXO, ("t1", quando)), _coleta(D3, FIXO, FIM))
    out = _suspeitos(_transactions(*_base(_txn("t1", dia))), raw, conferencia)
    return out.select("intervalo_de", "intervalo_ate", "intervalo_estado").row(0)


def test_intervalo_em_que_o_suspeito_cai():
    assert _intervalo("2026-06-02T10:00:00.000Z", "2026-06-02") == (
        _utc("2026-06-01T19:00:00.000Z"), _utc("2026-06-02T19:00:00.000Z"), NAO_FECHA)


def test_a_hora_vem_de_raw():
    # 02/06 às 21h é depois do sync das 19h: o intervalo é o seguinte.
    assert _intervalo("2026-06-02T21:00:00.000Z", "2026-06-02")[2] == FECHA


def test_borda_do_intervalo_e_de_menor_que_instante_menor_ou_igual_a_ate():
    assert _intervalo("2026-06-02T19:00:00.000Z", "2026-06-02")[2] == NAO_FECHA


@pytest.mark.parametrize("quando,dia", [("2026-06-01T10:00:00.000Z", "2026-06-01"),
                                        ("2026-06-01T19:00:00.000Z", "2026-06-01"),
                                        ("2026-06-04T10:00:00.000Z", "2026-06-04")])
def test_fora_de_todo_intervalo_fica_sem_intervalo(quando, dia):
    # Antes do primeiro snapshot (ou nele) e depois do último não há conferência.
    assert _intervalo(quando, dia) == (None, None, None)


def test_conta_sem_conferencia_fica_sem_intervalo():
    # Um cartão não tem conferência pelo saldo; uma conferência vazia também não quebra.
    assert _intervalo("2026-06-02T10:00:00.000Z", "2026-06-02", _conferencia()) == (None, None, None)
    outra = _conferencia((OUTRA, "2026-06-01T19:00:00.000Z", "2026-06-02T19:00:00.000Z", FECHA))
    assert _intervalo("2026-06-02T10:00:00.000Z", "2026-06-02", outra) == (None, None, None)


# --- a task -------------------------------------------------------------------------------------

def _seed_partition(tmp_path, day, frame):
    part = tmp_path / "raw" / "transactions" / f"ingestion_date={day}"
    part.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(part / "data.parquet")


def _seed(tmp_path):
    # s1 sumiu da coleta mais nova; a1 é resgate automático e segue vivo; ok não tem motivo.
    for dia, itens in ((D1, [FIXO, ("s1", "2026-06-02T10:00:00.000Z"), ("a1", "2026-06-03T10:00:00.000Z")]),
                       (D2, [FIXO, ("s1", "2026-06-02T10:00:00.000Z"), ("a1", "2026-06-03T10:00:00.000Z")]),
                       (D3, [FIXO, FIM, ("a1", "2026-06-03T10:00:00.000Z")])):
        _seed_partition(tmp_path, dia, _raw(_coleta(dia, *itens)).with_columns(amount=pl.lit("1.0")))
    _delta_util.seed(tmp_path, "cleansed", "transactions", _transactions(
        *_base(_txn("s1", "2026-06-02", -41.0), _txn("a1", "2026-06-03", 618.45, "Resgate RES APLIC AUT FICT"))))
    _delta_util.seed(tmp_path, "curated", "dim_account", pl.DataFrame(
        {"pluggy_account_id": [BANK], "account_uuid": ["banco-conta-0001"]}))
    _delta_util.seed(tmp_path, "curated", "conferencia_saldo", CONF.rename({"account_id": "account_uuid"})
                     .with_columns(account_uuid=pl.lit("banco-conta-0001"), diferenca=pl.lit(0.0)))


def _run(tmp_path):
    return _delta_util.run_script("cleansed2curated", "cleansed2curated_lancamentos_suspeitos",
                                  ["--source", "raw", "--target", "curated"], tmp_path)


def test_task_grava_por_account_uuid_sem_achado(tmp_path):
    _seed(tmp_path)
    _run(tmp_path)
    out = _delta_util.read(tmp_path, "curated", "lancamentos_suspeitos").sort("transaction_id")
    assert "account_id" not in out.columns
    assert out.select("transaction_id", "account_uuid", "motivo", "intervalo_estado").rows() == [
        ("a1", "banco-conta-0001", APLICACAO, FECHA), ("s1", "banco-conta-0001", SUMIU, NAO_FECHA)]
    findings = json.loads((tmp_path / "validation" / "curated.lancamentos_suspeitos.json").read_text())
    assert findings["findings"] == []


def test_task_imprime_contagem_por_motivo_sem_ids_nem_valores(tmp_path, capsys):
    _seed(tmp_path)
    _run(tmp_path)
    printed = capsys.readouterr().out
    assert ("lancamentos_suspeitos: 2 lançamentos, 2 marcas: sumiu_da_coleta 1, pendente_que_sumiu 0, "
            "aplicacao_automatica 1, uma_coleta_so 0") in printed
    assert not any(v in printed for v in ("s1", "a1", "34", "606", "banco-conta"))
