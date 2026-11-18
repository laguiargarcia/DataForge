import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "report"))

import polars as pl
from datetime import date
from report_lib import liquidity_per_month

def test_latest_snapshot_per_account_summed():
    # two snapshots same month/account → only the latest counts
    snap = pl.DataFrame({
        "account_uuid": ["bank","bank","card"],
        "snapshot_date": [date(2026,6,2), date(2026,6,20), date(2026,6,20)],
        "account_type": ["BANK","BANK","CREDIT"],
        "available": [10.0, 2874.15, 0.0],
        "balance": [10.0, 2874.15, 115.40],
        "card_outstanding": [0.0, 0.0, 115.40],
    })
    fact = pl.DataFrame({"date": [date(2026,6,5)], "account_type": ["BANK"], "amount_signed": [-1245.43]})
    out = liquidity_per_month(snap, fact).filter(pl.col("anomes_key")==202606).row(0, named=True)
    assert out["cash_disponivel"] == 2874.15
    assert out["caixa_saldo"] == 2874.15
    assert out["card_outstanding"] == 115.40
    assert round(out["saldo_disponivel"],2) == 2758.75   # cash − card_outstanding
    assert round(out["saidas_conta"],2) == 1245.43        # −Σ BANK outflows

def test_no_snapshot_month_blank():
    snap = pl.DataFrame({"account_uuid":["bank"],"snapshot_date":[date(2026,6,20)],
                         "account_type":["BANK"],"available":[100.0],"balance":[100.0],
                         "card_outstanding":[0.0]})
    fact = pl.DataFrame({"date": [date(2026,5,1)], "account_type":["BANK"], "amount_signed":[-5.0]})
    out = liquidity_per_month(snap, fact)
    assert 202605 not in out["anomes_key"].to_list()   # no fabricated carry-forward

def test_snapshot_month_no_bank_outflow_saidas_zero():
    # snapshot month exists but no BANK transactions → saidas_conta must be 0, not null
    snap = pl.DataFrame({
        "account_uuid": ["bank"],
        "snapshot_date": [date(2026,3,31)],
        "account_type": ["BANK"],
        "available": [500.0],
        "balance": [500.0],
        "card_outstanding": [0.0],
    })
    fact = pl.DataFrame({"date": pl.Series([], dtype=pl.Date),
                         "account_type": pl.Series([], dtype=pl.String),
                         "amount_signed": pl.Series([], dtype=pl.Float64)})
    out = liquidity_per_month(snap, fact).row(0, named=True)
    assert out["saidas_conta"] == 0.0

def test_empty_snap_returns_empty_with_schema():
    snap = pl.DataFrame({
        "account_uuid": pl.Series([], dtype=pl.String),
        "snapshot_date": pl.Series([], dtype=pl.Date),
        "account_type": pl.Series([], dtype=pl.String),
        "available": pl.Series([], dtype=pl.Float64),
        "balance": pl.Series([], dtype=pl.Float64),
        "card_outstanding": pl.Series([], dtype=pl.Float64),
    })
    fact = pl.DataFrame({"date": [date(2026,6,1)], "account_type": ["BANK"], "amount_signed": [-10.0]})
    out = liquidity_per_month(snap, fact)
    assert len(out) == 0
    assert set(out.columns) == {"anomes_key","mes_ano","cash_disponivel","caixa_saldo","card_outstanding","saldo_disponivel","saidas_conta"}
