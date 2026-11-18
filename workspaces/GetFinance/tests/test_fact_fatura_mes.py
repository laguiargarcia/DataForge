import polars as pl
from datetime import date

from tests import _delta_util


# Mirrors curated.fact_card_statement output schema (fact_card_statement.py).
STMT_SCHEMA = {
    "account_uuid": pl.Utf8, "statement_id": pl.Utf8, "due_date": pl.Date,
    "anomes_key": pl.Int64, "statement_amount": pl.Float64, "minimum_payment": pl.Float64,
    "paid_amount": pl.Float64, "paid_date": pl.Date, "payment_type": pl.Utf8,
    "paid_in_full": pl.Boolean, "finance_charges": pl.Float64,
    "allows_installments": pl.Boolean, "currency": pl.Utf8, "updated_at": pl.Utf8,
}

# Mirrors curated.fact_transaction schema (cleansed2curated.py, post-Task-2) — only the
# columns fact_fatura_mes.py reads, plus id/date for realism.
FACT_SCHEMA = {
    "id": pl.Utf8, "date": pl.Date, "account_uuid": pl.Utf8, "account_type": pl.Utf8,
    "amount": pl.Float64, "amount_signed": pl.Float64, "cc_bill_id": pl.Utf8,
    "cc_installment_number": pl.Int64, "cc_total_installments": pl.Int64,
    "cc_purchase_date": pl.Date,
}


def _seed(tmp_path, stmts, txns):
    s = pl.DataFrame({c: [r.get(c) for r in stmts] for c in STMT_SCHEMA}, schema=STMT_SCHEMA)
    t = pl.DataFrame({c: [r.get(c) for r in txns] for c in FACT_SCHEMA}, schema=FACT_SCHEMA)
    _delta_util.seed(tmp_path, "curated", "fact_card_statement", s)
    _delta_util.seed(tmp_path, "curated", "fact_transaction", t)
    # dim_account holds every account the facts carry (the script checks it, df-ahua.4.9).
    uuids = sorted({r["account_uuid"] for r in stmts + txns if r.get("account_uuid")})
    _delta_util.seed(tmp_path, "curated", "dim_account",
                     pl.DataFrame({"account_uuid": uuids, "pluggy_account_id": uuids},
                                  schema={"account_uuid": pl.Utf8, "pluggy_account_id": pl.Utf8}))


def _run(tmp_path):
    _delta_util.run_script("cleansed2curated", "fact_fatura_mes",
                           ["--source", "cleansed", "--target", "curated"], tmp_path)
    return _delta_util.read(tmp_path, "curated", "fact_fatura_mes")


def test_closed_bill_paid_in_full_has_zero_pendente(tmp_path):
    _seed(tmp_path, stmts=[{
        "account_uuid": "u1", "statement_id": "b1", "due_date": date(2026, 5, 17),
        "anomes_key": 202605, "statement_amount": 318.45, "paid_amount": 318.45,
        "paid_in_full": True,
    }], txns=[])
    out = _run(tmp_path)
    row = out.row(0, named=True)
    assert (row["anomes_key"], row["mes_inicio"]) == (202605, date(2026, 5, 1))
    assert row["valor_fatura"] == 318.45
    assert row["valor_pendente"] == 0.0
    assert row["is_open"] is False


def test_unpaid_closed_bill_pendente_equals_valor(tmp_path):
    _seed(tmp_path, stmts=[{
        "account_uuid": "u1", "statement_id": "b1", "due_date": date(2026, 5, 17),
        "anomes_key": 202605, "statement_amount": 200.0, "paid_amount": None,
    }], txns=[])
    out = _run(tmp_path)
    assert out.row(0, named=True)["valor_pendente"] == 200.0


def test_zero_bill_yields_zero_row(tmp_path):
    _seed(tmp_path, stmts=[{
        "account_uuid": "u1", "statement_id": "b1", "due_date": date(2025, 9, 17),
        "anomes_key": 202509, "statement_amount": 0.0, "paid_amount": None,
    }], txns=[])
    out = _run(tmp_path)
    row = out.row(0, named=True)
    assert (row["valor_fatura"], row["valor_pendente"]) == (0.0, 0.0)


def test_open_row_assigned_month_after_last_closed_bill(tmp_path):
    _seed(tmp_path,
          stmts=[{"account_uuid": "u1", "statement_id": "b1", "due_date": date(2026, 5, 17),
                  "anomes_key": 202605, "statement_amount": 318.45, "paid_amount": 318.45}],
          txns=[
              {"id": "t1", "date": date(2026, 5, 19), "account_uuid": "u1",
               "account_type": "CREDIT", "amount": 463.85, "cc_bill_id": None},
              {"id": "t2", "date": date(2026, 6, 2), "account_uuid": "u1",
               "account_type": "CREDIT", "amount": 100.0, "cc_bill_id": None},
              # billed charge — belongs to b1, must NOT enter the open row
              {"id": "t3", "date": date(2026, 4, 24), "account_uuid": "u1",
               "account_type": "CREDIT", "amount": 52.65, "cc_bill_id": "b1"},
          ])
    out = _run(tmp_path)
    open_row = out.filter(pl.col("is_open")).row(0, named=True)
    assert open_row["anomes_key"] == 202606
    assert open_row["mes_inicio"] == date(2026, 6, 1)
    assert open_row["due_date"] is None
    assert open_row["statement_id"] is None
    assert abs(open_row["valor_fatura"] - 563.85) < 0.001
    assert abs(open_row["valor_pendente"] - 563.85) < 0.001


def test_refund_reduces_open_sum(tmp_path):
    _seed(tmp_path,
          stmts=[{"account_uuid": "u1", "statement_id": "b1", "due_date": date(2026, 5, 17),
                  "anomes_key": 202605, "statement_amount": 10.0, "paid_amount": 10.0}],
          txns=[
              {"id": "t1", "date": date(2026, 5, 20), "account_uuid": "u1",
               "account_type": "CREDIT", "amount": 100.0, "cc_bill_id": None},
              {"id": "t2", "date": date(2026, 5, 21), "account_uuid": "u1",
               "account_type": "CREDIT", "amount": -30.0, "cc_bill_id": None},  # estorno
          ])
    out = _run(tmp_path)
    assert abs(out.filter(pl.col("is_open")).row(0, named=True)["valor_fatura"] - 70.0) < 0.001


def test_unmatched_bill_id_rows_are_dropped(tmp_path):
    _seed(tmp_path,
          stmts=[{"account_uuid": "u1", "statement_id": "b1", "due_date": date(2026, 5, 17),
                  "anomes_key": 202605, "statement_amount": 10.0, "paid_amount": 10.0}],
          txns=[{"id": "t1", "date": date(2026, 5, 20), "account_uuid": "u1",
                 "account_type": "CREDIT", "amount": 100.0, "cc_bill_id": "ghost-bill"}])
    out = _run(tmp_path)
    assert out.filter(pl.col("is_open")).height == 0  # not unbilled, not known — dropped


def test_no_open_row_when_nothing_unbilled(tmp_path):
    _seed(tmp_path,
          stmts=[{"account_uuid": "u1", "statement_id": "b1", "due_date": date(2026, 5, 17),
                  "anomes_key": 202605, "statement_amount": 10.0, "paid_amount": 10.0}],
          txns=[{"id": "t1", "date": date(2026, 4, 1), "account_uuid": "u1",
                 "account_type": "CREDIT", "amount": 10.0, "cc_bill_id": "b1"}])
    out = _run(tmp_path)
    assert out.filter(pl.col("is_open")).height == 0
    assert out.height == 1


def test_open_fallback_when_account_has_no_closed_bills(tmp_path):
    _seed(tmp_path, stmts=[],
          txns=[{"id": "t1", "date": date(2026, 6, 5), "account_uuid": "u1",
                 "account_type": "CREDIT", "amount": 50.0, "cc_bill_id": None}])
    out = _run(tmp_path)
    assert out.row(0, named=True)["anomes_key"] == 202607  # latest txn month + 1


def test_december_wraps_to_january(tmp_path):
    _seed(tmp_path,
          stmts=[{"account_uuid": "u1", "statement_id": "b1", "due_date": date(2025, 12, 17),
                  "anomes_key": 202512, "statement_amount": 10.0, "paid_amount": 10.0}],
          txns=[{"id": "t1", "date": date(2025, 12, 20), "account_uuid": "u1",
                 "account_type": "CREDIT", "amount": 5.0, "cc_bill_id": None}])
    out = _run(tmp_path)
    assert out.filter(pl.col("is_open")).row(0, named=True)["anomes_key"] == 202601


def test_bank_account_rows_ignored(tmp_path):
    _seed(tmp_path, stmts=[],
          txns=[{"id": "t1", "date": date(2026, 6, 5), "account_uuid": "u9",
                 "account_type": "BANK", "amount": 50.0, "cc_bill_id": None}])
    out = _run(tmp_path)
    assert out.height == 0


def test_null_due_date_statement_is_skipped_not_crashed(tmp_path):
    _seed(tmp_path, stmts=[{
        "account_uuid": "u1", "statement_id": "b-null", "due_date": None,
        "anomes_key": None, "statement_amount": 100.0, "paid_amount": None,
    }], txns=[])
    out = _run(tmp_path)
    # NULL-anomes rows must not appear in output; script must not crash
    assert out.height == 0


def test_null_anomes_statement_open_row_uses_txn_date_fallback(tmp_path):
    # C2: account whose only statement has NULL anomes_key AND has one unbilled CREDIT txn
    # → txn-date fallback must produce an open row with anomes_key 202607
    _seed(tmp_path, stmts=[{
        "account_uuid": "u1", "statement_id": "b-null", "due_date": None,
        "anomes_key": None, "statement_amount": 100.0, "paid_amount": None,
    }], txns=[{
        "id": "t1", "date": date(2026, 6, 5), "account_uuid": "u1",
        "account_type": "CREDIT", "amount": 50.0, "cc_bill_id": None,
    }])
    out = _run(tmp_path)
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["is_open"] is True
    assert row["anomes_key"] == 202607
