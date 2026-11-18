import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

import polars as pl
from datetime import date
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
