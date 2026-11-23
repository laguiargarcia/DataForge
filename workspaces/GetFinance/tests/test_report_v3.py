import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

import polars as pl
from datetime import date
from hypothesis import given, settings
from hypothesis import strategies as st
from report_lib import cartao_per_month


def _snap(rows):
    return pl.DataFrame(rows, schema={"account_uuid": pl.String, "snapshot_date": pl.Date,
                                      "account_type": pl.String, "balance": pl.Float64,
                                      "available": pl.Float64, "card_outstanding": pl.Float64,
                                      "credit_limit": pl.Float64, "available_credit_limit": pl.Float64},
                        orient="row")

def _fatura(rows):
    return pl.DataFrame(rows, schema={"account_uuid": pl.String, "anomes_key": pl.Int64,
                                      "valor_fatura": pl.Float64, "valor_pago": pl.Float64,
                                      "valor_pendente": pl.Float64, "is_open": pl.Boolean},
                        orient="row")

_FACT_SCHEMA = {"account_uuid": pl.String, "date": pl.Date, "account_type": pl.String,
                "description_normalized": pl.String,
                "amount": pl.Float64, "amount_signed": pl.Float64,
                "cc_installment_number": pl.Int64, "cc_total_installments": pl.Int64,
                "cc_purchase_date": pl.Date, "netting_applied": pl.Boolean}

def _fact(rows):
    return pl.DataFrame(rows, schema=_FACT_SCHEMA, orient="row")

_EMPTY_FACT = pl.DataFrame(schema=_FACT_SCHEMA)


def test_cartao_latest_snapshot_per_month():
    snap = _snap([
        ("card", date(2026, 6, 2), "CREDIT", 900.0, None, 900.0, 3200.0, 2300.0),
        ("card", date(2026, 6, 20), "CREDIT", 115.40, None, 115.40, 3200.0, 3084.60),
        ("bank", date(2026, 6, 20), "BANK", 2874.15, 2874.15, 0.0, None, None),
    ])
    out = cartao_per_month(snap, _fatura([]), _EMPTY_FACT)
    assert out["anomes_key"].to_list() == [202606]
    row = out.row(0, named=True)
    assert row["limite"] == 3200.0
    assert row["saldo_total"] == 115.40          # latest CREDIT snapshot only
    assert row["disponivel_limite"] == 3084.60
    assert row["mes_ano"] == "jun/2026"


def test_proxima_fatura_smallest_pending_month_at_or_after():
    snap = _snap([("card", date(2026, 6, 20), "CREDIT", 115.40, None, 115.40, 3200.0, 3084.60),
                  ("card", date(2026, 5, 31), "CREDIT", 1174.30, None, 1174.30, 3200.0, 2025.70)])
    fatura = _fatura([
        ("card", 202605, 318.45, 318.45, 0.0, False),     # settled — contributes nothing
        ("card", 202606, 1174.30, 1174.30, 0.0, False),   # settled
        ("card", 202607, 133.27, 0.0, 133.27, True),      # open cycle — the next obligation
    ])
    out = cartao_per_month(snap, fatura, _EMPTY_FACT)
    jun = out.filter(pl.col("anomes_key") == 202606).row(0, named=True)
    assert jun["proxima_fatura"] == 133.27
    mai = out.filter(pl.col("anomes_key") == 202605).row(0, named=True)
    assert mai["proxima_fatura"] == 133.27   # 202606 settled → next pending month is 202607


def test_proxima_fatura_zero_when_nothing_pending():
    snap = _snap([("card", date(2026, 6, 20), "CREDIT", 0.0, None, 0.0, 3200.0, 3200.0)])
    fatura = _fatura([("card", 202606, 100.0, 100.0, 0.0, False)])
    out = cartao_per_month(snap, fatura, _EMPTY_FACT)
    assert out.row(0, named=True)["proxima_fatura"] == 0.0


def test_comprometido_projection_and_netting_void():
    snap = _snap([("card", date(2026, 6, 20), "CREDIT", 500.0, None, 500.0, 3200.0, 2700.0)])
    fact = _fact([
        # Active series: 3 of 10 observed by June → 7 × 45.80 = 320.60 committed
        ("card", date(2026, 4, 10), "CREDIT", "SERIE 2026-4-10 10X", 45.80, -45.80, 1, 10, date(2026, 4, 10), False),
        ("card", date(2026, 5, 10), "CREDIT", "SERIE 2026-4-10 10X", 45.80, -45.80, 2, 10, date(2026, 4, 10), False),
        ("card", date(2026, 6, 10), "CREDIT", "SERIE 2026-4-10 10X", 45.80, -45.80, 3, 10, date(2026, 4, 10), False),
        # Netted series (df-0fc): base charge reversed → VOIDED, contributes 0
        ("card", date(2026, 6, 12), "CREDIT", "SERIE 2026-6-12 10X", 97.56, -97.56, 1, 10, date(2026, 6, 12), True),
        # Finished series: 2 of 2 → contributes 0
        ("card", date(2026, 5, 2), "CREDIT", "SERIE 2026-5-2 2X", 30.0, -30.0, 1, 2, date(2026, 5, 2), False),
        ("card", date(2026, 6, 2), "CREDIT", "SERIE 2026-5-2 2X", 30.0, -30.0, 2, 2, date(2026, 5, 2), False),
    ])
    out = cartao_per_month(snap, _fatura([]), fact)
    jun = out.row(0, named=True)
    assert round(jun["comprometido_parcelas"], 2) == 320.60


def test_comprometido_backcast_uses_only_rows_up_to_month():
    # As-of May only installments 1-2 of 10 are visible → 8 × 45.80 = 366.40
    snap = _snap([("card", date(2026, 5, 31), "CREDIT", 500.0, None, 500.0, 3200.0, 2700.0),
                  ("card", date(2026, 6, 20), "CREDIT", 500.0, None, 500.0, 3200.0, 2700.0)])
    fact = _fact([
        ("card", date(2026, 4, 10), "CREDIT", "SERIE 2026-4-10 10X", 45.80, -45.80, 1, 10, date(2026, 4, 10), False),
        ("card", date(2026, 5, 10), "CREDIT", "SERIE 2026-4-10 10X", 45.80, -45.80, 2, 10, date(2026, 4, 10), False),
        ("card", date(2026, 6, 10), "CREDIT", "SERIE 2026-4-10 10X", 45.80, -45.80, 3, 10, date(2026, 4, 10), False),
    ])
    out = cartao_per_month(snap, _fatura([]), fact)
    mai = out.filter(pl.col("anomes_key") == 202605).row(0, named=True)
    assert round(mai["comprometido_parcelas"], 2) == 366.40
    jun = out.filter(pl.col("anomes_key") == 202606).row(0, named=True)
    assert round(jun["comprometido_parcelas"], 2) == 320.60


def test_cartao_empty_snap_returns_empty_with_schema():
    out = cartao_per_month(_snap([]), _fatura([]), _EMPTY_FACT)
    assert len(out) == 0
    assert set(out.columns) == {"anomes_key", "mes_ano", "limite", "saldo_total",
                                "disponivel_limite", "proxima_fatura", "comprometido_parcelas"}


from report_lib import investimentos_per_month

_INV_FACT_SCHEMA = {"date": pl.Date, "account_type": pl.String, "amount_signed": pl.Float64,
                    "etl_tag": pl.String, "cash_flag": pl.Boolean, "operation_type": pl.String}

def _inv_fact(rows):
    return pl.DataFrame(rows, schema=_INV_FACT_SCHEMA, orient="row")

_POS_SCHEMA = {"holding_uuid": pl.String, "snapshot_date": pl.Date,
               "invested_amount": pl.Float64, "current_value": pl.Float64, "return": pl.Float64}

def _positions(rows):
    return pl.DataFrame(rows, schema=_POS_SCHEMA, orient="row")


def test_investimentos_flows_split_by_operation_type():
    fact = _inv_fact([
        (date(2026, 6, 5), "BANK", -250.0, "investment", True, "APLIC_FINANCEIRA"),
        (date(2026, 6, 12), "BANK", 250.0, "investment", True, "RESGATE_APLIC_FINANCEIRA"),
        (date(2026, 6, 20), "BANK", 0.47, "investment", True, "RENDIMENTO_APLIC_FINANCEIRA"),
        # PENDING investment row → cash_flag False → excluded (E4)
        (date(2026, 6, 25), "BANK", -100.0, "investment", False, "APLIC_FINANCEIRA"),
        # non-investment row → excluded
        (date(2026, 6, 7), "BANK", -50.0, None, True, None),
    ])
    out = investimentos_per_month(fact, _positions([]))
    jun = out.filter(pl.col("anomes_key") == 202606).row(0, named=True)
    assert jun["aplicado"] == 250.0
    assert jun["resgatado"] == 250.0
    assert round(jun["rendimento_conta"], 2) == 0.47
    assert jun["liquido"] == 0.0


def test_automatic_investment_lines_are_not_aplicado_nor_resgatado():
    # df-7twv.2: internal movement account <-> automatic investment, tagged on its own, is no investment flow.
    fact = _inv_fact([
        (date(2026, 6, 5), "BANK", -250.0, "investment", True, "APLIC_FINANCEIRA"),
        (date(2026, 6, 8), "BANK", -900.0, "aplicacao_automatica", True, "OUTROS"),
        (date(2026, 6, 9), "BANK", 600.0, "aplicacao_automatica", True, "RESGATE_APLIC_FINANCEIRA"),
    ])
    jun = investimentos_per_month(fact, _positions([])).filter(pl.col("anomes_key") == 202606).row(0, named=True)
    assert (jun["aplicado"], jun["resgatado"]) == (250.0, 0.0)


def test_investimentos_positions_latest_per_holding_per_month():
    pos = _positions([
        ("h1", date(2026, 6, 2), 500.0, 480.0, -20.0),
        ("h1", date(2026, 6, 20), 500.0, 470.0, -30.0),   # latest for h1 in June
        ("h2", date(2026, 6, 20), 1080.0, 548.21, -531.79),
    ])
    out = investimentos_per_month(_inv_fact([]), pos)
    jun = out.filter(pl.col("anomes_key") == 202606).row(0, named=True)
    assert round(jun["posicao"], 2) == 1018.21          # 470 + 548.21
    # principal = Σ(current − return) (df-9uv): (470+30) + (548.21+531.79) — here return ==
    # current − invested on every row, so the value matches the old Σ invested_amount.
    assert round(jun["principal"], 2) == 1580.0
    assert round(jun["rentabilidade"], 2) == -561.79
    assert jun["aplicado"] == 0.0                       # flow month without flows → 0.0


def test_investimentos_principal_is_posicao_minus_rentabilidade():
    # df-9uv (a holding with a withdrawal): invested_amount is gross-ever-deposited (1080 despite a
    # partial withdrawal) and return is Pluggy's own P&L (+2.98) — return ≠ current − invested.
    # principal must follow Σ(current − coalesce(return, 0)), NOT Σ invested_amount.
    pos = _positions([
        ("h-modelo", date(2026, 6, 20), 1080.0, 536.97, 2.98),
        ("h-null", date(2026, 6, 20), None, 1.81, None),   # NULL return → contributes current
    ])
    out = investimentos_per_month(_inv_fact([]), pos)
    jun = out.filter(pl.col("anomes_key") == 202606).row(0, named=True)
    assert round(jun["posicao"], 2) == 538.78              # 536.97 + 1.81
    assert round(jun["principal"], 2) == 535.80            # (536.97 − 2.98) + 1.81, NOT 1081.81
    assert round(jun["rentabilidade"], 2) == 2.98
    assert round(jun["principal"] + jun["rentabilidade"], 2) == round(jun["posicao"], 2)


def test_investimentos_flow_month_without_positions_is_null():
    fact = _inv_fact([(date(2026, 5, 5), "BANK", -250.0, "investment", True, "APLIC_FINANCEIRA")])
    pos = _positions([("h1", date(2026, 6, 20), 500.0, 470.0, -30.0)])
    out = investimentos_per_month(fact, pos)
    mai = out.filter(pl.col("anomes_key") == 202605).row(0, named=True)
    assert mai["posicao"] is None                        # no fabricated carry-forward
    assert mai["aplicado"] == 250.0


def test_investimentos_empty_inputs():
    out = investimentos_per_month(_inv_fact([]), _positions([]))
    assert len(out) == 0
    assert set(out.columns) == {"anomes_key", "mes_ano", "aplicado", "resgatado",
                                "rendimento_conta", "liquido", "posicao", "principal",
                                "rentabilidade"}


from report_lib import liquidity_per_month, extend_liquidity_v3


def test_liquidity_gains_caixa_saldo():
    snap = _snap([
        ("bank", date(2026, 6, 20), "BANK", 228.15, 210.0, 0.0, None, None),
        ("card", date(2026, 6, 20), "CREDIT", 318.39, None, 318.39, 2850.0, 2531.61),
    ])
    fact = pl.DataFrame({"date": [date(2026, 6, 5)], "account_type": ["BANK"], "amount_signed": [-10.0]})
    out = liquidity_per_month(snap, fact).row(0, named=True)
    assert out["caixa_saldo"] == 228.15        # balance, not available
    assert out["cash_disponivel"] == 210.0     # pre-existing column unchanged


def test_extend_liquidity_v3_identities():
    snap = _snap([
        ("bank", date(2026, 6, 20), "BANK", 228.15, 228.15, 0.0, None, None),
        ("card", date(2026, 6, 20), "CREDIT", 318.39, None, 318.39, 2850.0, 2531.61),
    ])
    fact = pl.DataFrame({"date": [date(2026, 6, 5)], "account_type": ["BANK"], "amount_signed": [-10.0]})
    liq = liquidity_per_month(snap, fact)
    cartao = pl.DataFrame({"anomes_key": pl.Series([202606], dtype=pl.Int32),
                           "proxima_fatura": [133.27]})
    invest = pl.DataFrame({"anomes_key": pl.Series([202606], dtype=pl.Int32),
                           "posicao": [1018.21]})
    out = extend_liquidity_v3(liq, cartao, invest).row(0, named=True)
    assert round(out["disponivel_gastar"], 2) == round(228.15 - 133.27, 2)      # 94.88
    assert round(out["patrimonio"], 2) == round(228.15 + 1018.21 - 318.39, 2)   # 927.97
    assert out["investimentos_posicao"] == 1018.21


def test_extend_liquidity_v3_missing_months_degrade():
    snap = _snap([("bank", date(2026, 6, 20), "BANK", 100.0, 100.0, 0.0, None, None)])
    fact = pl.DataFrame({"date": pl.Series([], dtype=pl.Date),
                         "account_type": pl.Series([], dtype=pl.String),
                         "amount_signed": pl.Series([], dtype=pl.Float64)})
    liq = liquidity_per_month(snap, fact)
    empty_cartao = pl.DataFrame({"anomes_key": pl.Series([], dtype=pl.Int32),
                                 "proxima_fatura": pl.Series([], dtype=pl.Float64)})
    empty_invest = pl.DataFrame({"anomes_key": pl.Series([], dtype=pl.Int32),
                                 "posicao": pl.Series([], dtype=pl.Float64)})
    out = extend_liquidity_v3(liq, empty_cartao, empty_invest).row(0, named=True)
    assert out["proxima_fatura"] == 0.0            # no fatura → nothing to pay
    assert out["disponivel_gastar"] == 100.0
    assert out["investimentos_posicao"] is None    # unknown ≠ zero
    assert out["patrimonio"] is None               # incomplete inputs → null, never a wrong number


def test_investimentos_follow_user_corrections():
    # df-cns.8: a correction beats etl_tag here too. "a" was tagged investment and corrected to
    # Groceries (leaves the flows); "b" had no tag and was corrected to Investimentos (enters);
    # "c" is tagged and uncorrected (stays); a correction to a non-investment subcategory of an
    # untagged row ("d") changes nothing.
    fact = pl.DataFrame({
        "id": ["a", "b", "c", "d"],
        "date": [date(2026, 6, 5)] * 4,
        "account_type": ["BANK"] * 4,
        "amount_signed": [-100.0, -40.0, -7.0, -3.0],
        "etl_tag": ["investment", None, "investment", None],
        "cash_flag": [True] * 4,
        "operation_type": [None] * 4,
    })
    corrections = pl.DataFrame({"id": ["a", "b", "d"], "subcategoria": ["Groceries", "Investimentos", "Contas"]})
    jun = investimentos_per_month(fact, _positions([]), corrections).row(0, named=True)
    assert jun["aplicado"] == 47.0
    assert investimentos_per_month(fact, _positions([])).row(0, named=True)["aplicado"] == 107.0
    assert investimentos_per_month(fact, _positions([]), corrections.clear()).row(0, named=True)["aplicado"] == 107.0


# df-dsy4: a matured holding the bank keeps sending as ACTIVE, frozen, leaves the position only once
# its redemption reached the account (an investment credit on or after the maturity day).
_HOLD_SCHEMA = {"holding_uuid": pl.String, "due_date": pl.Date}

def _holdings(rows):
    return pl.DataFrame(rows, schema=_HOLD_SCHEMA, orient="row")

def _tesouro_congelado():
    # Vence em 15/08 (sábado); o banco segue mandando o saldo congelado em set e out.
    return _positions([
        ("tesouro", date(2026, 8, 11), 16000.0, 18000.0, 2000.0),
        ("tesouro", date(2026, 8, 30), 16000.0, 18040.5, 2040.5),
        ("tesouro", date(2026, 9, 30), 16000.0, 18040.5, 2040.5),
        ("tesouro", date(2026, 10, 30), 16000.0, 18040.5, 2040.5),
        ("cdb", date(2026, 8, 30), 1000.0, 1000.0, 0.0),
        ("cdb", date(2026, 9, 30), 1000.0, 1000.0, 0.0),
        ("cdb", date(2026, 10, 30), 1000.0, 1000.0, 0.0),
    ])

def _posicao_por_mes(fact, pos, holdings):
    out = investimentos_per_month(fact, pos, holdings=holdings)
    return dict(zip(out["anomes_key"].to_list(), out["posicao"].to_list(), strict=True))

_HOLD = [("tesouro", date(2026, 8, 15)), ("cdb", None)]


def test_matured_holding_leaves_position_after_its_redemption_reaches_the_account():
    fact = _inv_fact([(date(2026, 8, 17), "BANK", 19100.0, "investment", True, "OUTROS")])
    assert _posicao_por_mes(fact, _tesouro_congelado(), _holdings(_HOLD)) == {
        202608: 1000.0, 202609: 1000.0, 202610: 1000.0}


def test_redemption_in_a_later_month_keeps_the_holding_until_then():
    fact = _inv_fact([(date(2026, 8, 25), "BANK", 17500.0, "investment", True, "OUTROS")])
    pos = _positions([("tesouro", date(2026, 8, 15), 16000.0, 17500.0, 1500.0),
                      ("tesouro", date(2026, 8, 20), 16000.0, 17500.0, 1500.0)])
    hold = _holdings([("tesouro", date(2026, 8, 15))])
    # The month's latest snapshot (20/08) is before the credit (25/08): still invested in August.
    assert _posicao_por_mes(fact, pos, hold) == {202608: 17500.0}


def test_matured_holding_without_redemption_credit_keeps_counting():
    sem_prova = [
        _inv_fact([]),
        _inv_fact([(date(2026, 8, 14), "BANK", 19100.0, "investment", True, "OUTROS")]),  # antes do vencimento
        _inv_fact([(date(2026, 8, 26), "BANK", 19100.0, "investment", True, "OUTROS")]),  # fora da janela
        _inv_fact([(date(2026, 8, 17), "BANK", 15000.0, "investment", True, "OUTROS")]),   # pequeno demais
        _inv_fact([(date(2026, 8, 17), "BANK", 19100.0, None, True, "OUTROS")]),           # não é investimento
        _inv_fact([(date(2026, 8, 17), "BANK", 19100.0, "investment", False, "OUTROS")]),  # pendente
        _inv_fact([(date(2026, 8, 17), "BANK", -19100.0, "investment", True, "OUTROS")]),  # débito
    ]
    for fact in sem_prova:
        assert _posicao_por_mes(fact, _tesouro_congelado(), _holdings(_HOLD))[202610] == 19040.5


def test_one_credit_proves_one_matured_holding():
    pos = _positions([("a", date(2026, 8, 10), 900.0, 1000.0, 100.0),
                      ("b", date(2026, 8, 10), 900.0, 1000.0, 100.0),
                      ("a", date(2026, 9, 30), 900.0, 1000.0, 100.0),
                      ("b", date(2026, 9, 30), 900.0, 1000.0, 100.0)])
    hold = _holdings([("a", date(2026, 8, 15)), ("b", date(2026, 8, 15))])
    um = _inv_fact([(date(2026, 8, 16), "BANK", 1000.0, "investment", True, "OUTROS")])
    dois = _inv_fact([(date(2026, 8, 16), "BANK", 1000.0, "investment", True, "OUTROS"),
                      (date(2026, 8, 18), "BANK", 1000.0, "investment", True, "OUTROS")])
    assert _posicao_por_mes(um, pos, hold)[202609] == 1000.0
    assert _posicao_por_mes(dois, pos, hold)[202609] == 0.0   # all redeemed: 0, not "no data"


def test_without_due_dates_the_position_is_unchanged():
    fact = _inv_fact([(date(2026, 8, 17), "BANK", 19100.0, "investment", True, "OUTROS")])
    sem = investimentos_per_month(fact, _tesouro_congelado())
    assert investimentos_per_month(fact, _tesouro_congelado(),
                                   holdings=_holdings([("tesouro", None), ("cdb", None)])).equals(sem)
    antigo = pl.DataFrame({"holding_uuid": ["tesouro"]})            # dim_investment from before df-dsy4
    assert investimentos_per_month(fact, _tesouro_congelado(), holdings=antigo).equals(sem)


# --- propriedades de investimentos_per_month (df-xkhx.4) ---------------------------------------
# Valores inteiros: as somas em float saem exatas em qualquer ordem.
_dias = st.dates(date(2026, 5, 1), date(2026, 7, 31))
_fluxos = st.lists(st.tuples(
    _dias, st.just("BANK"), st.integers(-1000, 1000).map(float), st.sampled_from(["investment", None, "outra"]),
    st.sampled_from([True, True, False, None]),
    st.sampled_from(["APLIC_FINANCEIRA", "RESGATE_APLIC_FINANCEIRA", "RENDIMENTO_APLIC_FINANCEIRA", None]),
), max_size=12)
_valor = st.none() | st.integers(0, 1000).map(float)
_snapshots = st.dictionaries(st.tuples(st.sampled_from(["h1", "h2", "h3"]), _dias),
                             st.tuples(_valor, st.none() | st.integers(-100, 100).map(float)), max_size=10)


def _anomes(d):
    return d.year * 100 + d.month


@settings(max_examples=150, deadline=None)
@given(fluxos=_fluxos, snapshots=_snapshots)
def test_propriedade_investimentos_fluxo_estrutural_e_posicao_do_ultimo_snapshot(fluxos, snapshots):
    pos = _positions([(h, d, 0.0, valor, retorno) for (h, d), (valor, retorno) in snapshots.items()])
    out = investimentos_per_month(_inv_fact(fluxos), pos)
    # Só a linha de investimento que passou pela conta conta; as outras não mudam nada.
    estruturais = [f for f in fluxos if f[3] == "investment" and f[4]]
    assert investimentos_per_month(_inv_fact(estruturais), pos).equals(out)
    assert out["anomes_key"].to_list() == sorted({_anomes(f[0]) for f in estruturais}
                                                 | {_anomes(d) for _, d in snapshots})
    for r in out.iter_rows(named=True):
        do_mes = [f for f in estruturais if _anomes(f[0]) == r["anomes_key"]]
        assert r["aplicado"] == -sum(f[2] for f in do_mes if f[2] < 0)
        assert r["resgatado"] == sum(f[2] for f in do_mes if f[2] > 0 and f[5] == "RESGATE_APLIC_FINANCEIRA")
        assert r["rendimento_conta"] == sum(
            f[2] for f in do_mes if f[2] > 0 and f[5] == "RENDIMENTO_APLIC_FINANCEIRA")
        assert r["liquido"] == r["aplicado"] - r["resgatado"]
        # Posição: o último snapshot de cada aplicação no mês; mês sem snapshot fica nulo, sem carregar o anterior.
        ultimo = {}
        for (h, d), (valor, retorno) in sorted(snapshots.items()):
            if _anomes(d) == r["anomes_key"]:
                ultimo[h] = (valor or 0.0, retorno or 0.0)
        if not ultimo:
            assert (r["posicao"], r["principal"], r["rentabilidade"]) == (None, None, None)
            continue
        assert r["posicao"] == sum(valor for valor, _ in ultimo.values())
        assert r["rentabilidade"] == sum(retorno for _, retorno in ultimo.values())
        assert r["posicao"] == r["principal"] + r["rentabilidade"]
