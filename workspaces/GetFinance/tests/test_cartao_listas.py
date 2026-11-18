"""Parcelamentos ativos e itens da próxima fatura (df-cns etapa 1, Task 4) — PQ cc_parcelas e cc_fatura_itens."""
import sys
from datetime import date
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

from cartao_listas import itens_proxima_fatura, parcelas_ativas, ultimo_mes

_FACT = {"id": pl.String, "date": pl.Date, "account_uuid": pl.String, "account_type": pl.String,
         "description_normalized": pl.String, "amount": pl.Float64, "cc_bill_id": pl.String,
         "cc_installment_number": pl.Int64, "cc_total_installments": pl.Int64,
         "cc_purchase_date": pl.Date, "netting_applied": pl.Boolean, "etl_tag": pl.String}
_FATURA = {"anomes_key": pl.Int64, "statement_id": pl.String, "valor_pendente": pl.Float64}
_COLS_PARCELAS = ["anomes_key", "compra", "cartao", "categoria", "valor_parcela", "pagas", "restam",
                  "valor_restante", "comprado_em"]
_COLS_ITENS = ["anomes_key", "data", "descricao", "categoria", "parcela", "valor"]


def _parc(id_, d, desc, kn, bill, **opts):
    """kn = (k, n). opts: amount (100.0), netting (False), compra (2026-04-01)."""
    k, n = kn
    return (id_, d, "c1", "CREDIT", desc, opts.get("amount", 100.0), bill, k, n,
            opts.get("compra", date(2026, 4, 1)), opts.get("netting", False), None)


def _fact(rows):
    return pl.DataFrame(rows, schema=_FACT, orient="row")


def _classificada(fact):
    return fact.filter(~pl.col("netting_applied")).with_columns(
        pl.concat_str(pl.lit("cat-"), pl.col("id")).alias("categoria"))


def _fatura(rows):
    return pl.DataFrame(rows, schema=_FATURA, orient="row")


FAT = _fatura([(202604, "b4", 0.0), (202605, "b5", 0.0), (202606, "b6", 100.0), (202607, "b7", 100.0)])
SERIE = [_parc("p1", date(2026, 4, 10), "LOJA X 01/05", (1, 5), "b4"),
         _parc("p2", date(2026, 5, 10), "LOJA X 02/05", (2, 5), "b5"),
         _parc("p3", date(2026, 6, 10), "LOJA X 03/05", (3, 5), "b6")]


def _parcelas(rows, mes=202606, fatura=FAT):
    fact = _fact(rows)
    return parcelas_ativas(fact, _classificada(fact), fatura, mes)


def test_ultimo_mes():
    assert ultimo_mes(pl.DataFrame({"anomes_key": [202605, 202607]})) == 202607
    assert ultimo_mes(pl.DataFrame(schema={"anomes_key": pl.Int32})) is None


def test_serie_ativa():
    df = _parcelas(SERIE)
    assert df.columns == _COLS_PARCELAS
    r = df.row(0, named=True)
    assert df.height == 1
    assert (r["compra"], r["cartao"], r["categoria"], r["pagas"], r["restam"]) == ("LOJA X", "c1", "cat-p3", "3/5", 2)
    assert (r["valor_parcela"], r["valor_restante"], r["comprado_em"], r["anomes_key"]) == (
        100.0, 200.0, date(2026, 4, 1), 202606)


def test_serie_anulada_some():
    # A linha anulada é posterior ao mês do report e mesmo assim anula a série (PQ: VoidKeys sem corte).
    assert _parcelas(SERIE + [_parc("p4", date(2026, 7, 10), "LOJA X 04/05", (4, 5), "b7", netting=True)]).height == 0


def test_corte_no_mes():
    df = _parcelas(SERIE + [_parc("p4", date(2026, 7, 10), "LOJA X 04/05", (4, 5), "b7")])
    assert df["pagas"].to_list() == ["3/5"]


def test_series_intercaladas():
    rows = [_parc("a1", date(2026, 5, 3), "AMAZON 01/03", (1, 3), "b5", compra=None),
            _parc("a2", date(2026, 6, 3), "AMAZON 02/03", (2, 3), "b6", compra=None),
            _parc("b1", date(2026, 6, 5), "AMAZON 01/03", (1, 3), "b6", compra=None)]
    df = _parcelas(rows)
    assert sorted(df["pagas"].to_list()) == ["1/3", "2/3"]
    assert df.filter(pl.col("pagas") == "2/3")["comprado_em"].item() == date(2026, 5, 3)


def test_serie_quitada_fora():
    rows = [_parc("q1", date(2026, 5, 10), "CURSO 01/02", (1, 2), "b5"),
            _parc("q2", date(2026, 6, 10), "CURSO 02/02", (2, 2), "b6")]
    assert _parcelas(rows).height == 0


def _item(id_, d, bill, kn=(None, None), tag=None):
    k, n = kn
    return (id_, d, "c1", "CREDIT", f"ITEM {id_}", 10.0, bill, k, n, None, False, tag)


def test_itens_fatura_alvo():
    fat = _fatura([(202604, "s4", 99.0), (202605, "s5", 0.0), (202606, "s6", 50.0)])
    fact = _fact([_item("i2", date(2026, 6, 9), "s6", (2, 3)), _item("i1", date(2026, 6, 2), "s6"),
                  _item("i3", date(2026, 6, 4), "s6", tag="card_payment_card"),
                  _item("i4", date(2026, 5, 4), "s5")])
    df = itens_proxima_fatura(_classificada(fact), fat, 202606)
    assert df.columns == _COLS_ITENS
    assert df["descricao"].to_list() == ["ITEM i1", "ITEM i2"]
    assert df["parcela"].to_list() == ["à vista", "2/3"]
    assert df["categoria"].to_list() == ["cat-i1", "cat-i2"]
    assert df["anomes_key"].to_list() == [202606, 202606]


def test_itens_ciclo_aberto():
    fat = _fatura([(202606, None, 30.0)])
    fact = _fact([_item("o1", date(2026, 6, 2), None), _item("o2", date(2026, 6, 3), "s6")])
    assert itens_proxima_fatura(_classificada(fact), fat, 202606)["descricao"].to_list() == ["ITEM o1"]


def test_listas_vazias():
    fact = _fact([])
    fat = _fatura([])
    for mes in (None, 202606):
        p = parcelas_ativas(fact, _classificada(fact), fat, mes)
        i = itens_proxima_fatura(_classificada(fact), fat, mes)
        assert p.height == 0
        assert p.columns == _COLS_PARCELAS
        assert i.height == 0
        assert i.columns == _COLS_ITENS


def test_empate_de_data_usa_a_primeira_linha():
    # PQ: Table.Sort(date desc){0} — entre parcelas da série com a mesma data mais recente, vale a
    # PRIMEIRA na ordem das linhas (banco que data todas as parcelas pela compra).
    # Faturas b5 (k=1) e b6 (k=2): mesmo anchor, mesma série; as duas datadas no mesmo dia.
    rows = [_parc("e1", date(2026, 6, 10), "LOJA Y 01/04", (1, 4), "b5", amount=100.0),
            _parc("e2", date(2026, 6, 10), "LOJA Y 02/04", (2, 4), "b6", amount=101.0)]
    df = _parcelas(rows)
    assert df.height == 1
    r = df.row(0, named=True)
    assert (r["valor_parcela"], r["categoria"]) == (100.0, "cat-e1")
