import polars as pl
import pytest
from datetime import date
from pathlib import Path

from tests import _delta_util

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# df-hog behavior (payment reattribution / re-issued-bill dedup / bank-leg settlement) ships
# Dev-first; prod runs the old script until the Resumo redesign promotes (df-668).

# cleansed.bills is the typed SCD-1 table raw2cleansed_bills.py produces. payments / financeCharges
# remain repr-string blobs (parsed in fact_card_statement.py via ast.literal_eval).
BILLS_SCHEMA = {
    "id": pl.Utf8, "accountId": pl.Utf8, "dueDate": pl.Utf8, "totalAmount": pl.Float64,
    "totalAmountCurrencyCode": pl.Utf8, "minimumPaymentAmount": pl.Float64,
    "financeCharges": pl.Utf8, "payments": pl.Utf8, "allowsInstallments": pl.Boolean,
    "updatedAt": pl.Utf8, "ingested_at": pl.Utf8,
}

# Live shape: a single FULL_PAYMENT covering the whole statement.
PAY_FULL = ("[{'valueType': 'FULL_PAYMENT', 'paymentDate': '2026-05-18T00:00:00.000Z', "
            "'amount': 318.45, 'paymentMode': 'DEBIT_ACCOUNT', 'currencyCode': 'BRL'}]")
# Synthetic: a single non-full payment under the statement total → PARTIAL.
PAY_PARTIAL = ("[{'valueType': 'PARTIAL_PAYMENT', 'paymentDate': '2026-04-18T00:00:00.000Z', "
               "'amount': 100.0, 'paymentMode': 'DEBIT_ACCOUNT', 'currencyCode': 'BRL'}]")
# Synthetic: two distinct value types → MIXED (sum covers the total here).
PAY_MIXED = ("[{'valueType': 'PARTIAL_PAYMENT', 'paymentDate': '2026-03-15T00:00:00.000Z', "
             "'amount': 50.0, 'paymentMode': 'DEBIT_ACCOUNT', 'currencyCode': 'BRL'}, "
             "{'valueType': 'FULL_PAYMENT', 'paymentDate': '2026-03-18T00:00:00.000Z', "
             "'amount': 150.0, 'paymentMode': 'DEBIT_ACCOUNT', 'currencyCode': 'BRL'}]")


def _seed_bills(tmp_path: Path, rows: list[dict]) -> None:
    cols = list(BILLS_SCHEMA)
    data = {c: [r.get(c) for r in rows] for c in cols}
    df = pl.DataFrame(data, schema=BILLS_SCHEMA)
    _delta_util.seed(tmp_path, "cleansed", "bills", df)


def _seed_dim_account(tmp_path: Path, mapping: dict) -> None:
    df = pl.DataFrame(
        {
            "account_uuid": list(mapping.values()),
            "pluggy_account_id": list(mapping.keys()),
            "name": ["n" for _ in mapping],
            "institution": ["Banco Fictício" for _ in mapping],
            "type": ["CREDIT" for _ in mapping],
            "currency": ["BRL" for _ in mapping],
        },
        schema={
            "account_uuid": pl.Utf8, "pluggy_account_id": pl.Utf8, "name": pl.Utf8,
            "institution": pl.Utf8, "type": pl.Utf8, "currency": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "curated", "dim_account", df)


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("cleansed2curated", "fact_card_statement",
                           ["--source", "cleansed", "--target", "curated"], tmp_path)


def _read_fact(tmp_path: Path) -> list[dict]:
    return (
        _delta_util.read(tmp_path, "curated", "fact_card_statement")
        .sort("account_uuid", "due_date")
        .to_dicts()
    )


def _full_bill(**over) -> dict:
    base = {
        "id": "bill-may", "accountId": "acc-card", "dueDate": "2026-05-17T00:00:00.000Z",
        "totalAmount": 318.45, "totalAmountCurrencyCode": "BRL", "minimumPaymentAmount": 31.85,
        "financeCharges": "[]", "payments": PAY_FULL, "allowsInstallments": True,
        "updatedAt": "2026-05-19T00:00:00.000Z", "ingested_at": "2026-06-04",
    }
    base.update(over)
    return base


def test_14_col_schema(tmp_path):
    _seed_bills(tmp_path, [_full_bill()])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "curated", "fact_card_statement")
    assert df.columns == [
        "account_uuid", "statement_id", "due_date", "anomes_key", "statement_amount",
        "minimum_payment", "paid_amount", "paid_date", "payment_type", "paid_in_full",
        "finance_charges", "allows_installments", "currency", "updated_at",
    ]
    assert df.schema["due_date"] == pl.Date
    assert df.schema["anomes_key"] == pl.Int64
    assert df.schema["paid_in_full"] == pl.Boolean
    assert df.schema["allows_installments"] == pl.Boolean


def test_full_paid_bill(tmp_path):
    # A due-2026-05-17 bill: anomes 202605, paid in full on 2026-05-18.
    _seed_bills(tmp_path, [_full_bill()])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["account_uuid"] == "acme-credit"
    assert r["statement_id"] == "bill-may"
    assert str(r["due_date"]) == "2026-05-17"
    assert r["anomes_key"] == 202605
    assert r["statement_amount"] == 318.45
    assert r["minimum_payment"] == 31.85
    assert r["paid_amount"] == 318.45
    assert str(r["paid_date"]) == "2026-05-18"
    assert r["payment_type"] == "FULL_PAYMENT"
    assert r["paid_in_full"] is True
    assert r["finance_charges"] == 0.0
    assert r["allows_installments"] is True
    assert r["currency"] == "BRL"


def test_anomes_key_is_due_month(tmp_path):
    _seed_bills(tmp_path, [_full_bill(id="b", dueDate="2025-09-17T00:00:00.000Z")])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["anomes_key"] == 202509


def test_zero_total_no_payments_not_paid_in_full(tmp_path):
    # A Sep-2025 zero-total bill with empty payments MUST be paid_in_full False — the
    # payments-present gate prevents 0 >= -0.01 mislabeling it True.
    _seed_bills(tmp_path, [_full_bill(
        id="bill-zero", dueDate="2025-09-17T00:00:00.000Z", totalAmount=0.0,
        minimumPaymentAmount=0.0, payments="[]", allowsInstallments=False)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["statement_amount"] == 0.0
    assert r["paid_amount"] is None
    assert r["paid_date"] is None
    assert r["payment_type"] is None
    assert r["paid_in_full"] is False, "a $0 bill with no payments must NOT be paid_in_full"


def test_partial_payment_not_paid_in_full(tmp_path):
    # A single under-total payment → PARTIAL, paid_in_full False.
    _seed_bills(tmp_path, [_full_bill(
        id="bill-partial", dueDate="2026-04-17T00:00:00.000Z", totalAmount=318.45,
        payments=PAY_PARTIAL)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["payment_type"] == "PARTIAL"
    assert r["paid_amount"] == 100.0
    assert str(r["paid_date"]) == "2026-04-18"
    assert r["paid_in_full"] is False


def test_mixed_payment_type(tmp_path):
    # Two distinct value types → MIXED; their sum (200.0) covers the 200.0 total → paid_in_full.
    _seed_bills(tmp_path, [_full_bill(
        id="bill-mixed", dueDate="2026-03-17T00:00:00.000Z", totalAmount=200.0,
        payments=PAY_MIXED)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["payment_type"] == "MIXED"
    assert r["paid_amount"] == 200.0
    assert str(r["paid_date"]) == "2026-03-18", "paid_date is the max payment date"
    assert r["paid_in_full"] is True


def test_null_safe_empty_payments_and_finance_charges(tmp_path):
    # NULL payments/financeCharges blobs (not even "[]") must not crash; paid_* NULL, fc 0.0.
    _seed_bills(tmp_path, [_full_bill(
        id="bill-null", totalAmount=50.0, payments=None, financeCharges=None)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["paid_amount"] is None
    assert r["paid_date"] is None
    assert r["payment_type"] is None
    assert r["paid_in_full"] is False
    assert r["finance_charges"] == 0.0


def test_finance_charges_summed(tmp_path):
    # Defensive finance_charges aggregation over a non-empty blob (structure unconfirmed live).
    fc = ("[{'type': 'INTEREST', 'amount': 12.5, 'currencyCode': 'BRL'}, "
          "{'type': 'LATE_FEE', 'amount': 7.5, 'currencyCode': 'BRL'}]")
    _seed_bills(tmp_path, [_full_bill(id="bill-fc", financeCharges=fc)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["finance_charges"] == 20.0


def test_bill_of_an_account_missing_from_dim_account_is_refused(tmp_path):
    # dim_account has every account (D3), so a bill the inner join would drop is a lost bill: the
    # reconciliation refuses the write (df-ahua.4.9, df-ahua.4.11).
    _seed_bills(tmp_path, [_full_bill()])
    _seed_dim_account(tmp_path, {"some-other-id": "acme-credit"})
    with pytest.raises(Exception, match="curated.fact_card_statement vs cleansed: rows not carried") as exc:
        _run(tmp_path)
    assert type(exc.value).__name__ == "DataValidationError"
    assert not (tmp_path / "curated" / "fact_card_statement").exists()


def test_no_duckdb_import():
    src = (SCRIPTS / "cleansed2curated" / "fact_card_statement.py").read_text()
    assert "import duckdb" not in src


# --------------------------------------------------------------------------- #
# df-hog: payment reattribution + re-issued-bill dedup + bank-leg settlement   #
# Evidence (2026-07-02): since the 2026-06-17 Pluggy shape change, a bill's    #
# payments[] carries the PREVIOUS cycle's settlement (OTHER_PAYMENT/BANK_SLIP, #
# exact amount+date of the prior bill's payment); the May statement was        #
# re-issued under a new id (charges re-keyed to it) while the old id is still  #
# returned; and débito-automático settlements never appear on the bill at all  #
# — only as a BANK 05100000 transaction (and later on the NEXT statement).     #
# --------------------------------------------------------------------------- #
def _pay(entries) -> str:
    """Render a payments[] repr-blob from (valueType, paymentDate, amount, mode) tuples."""
    items = [
        {"id": f"p{i}", "valueType": vt, "paymentDate": f"{d}T00:00:00.000Z",
         "paymentMode": mode, "amount": amt, "currencyCode": "BRL"}
        for i, (vt, d, amt, mode) in enumerate(entries)
    ]
    return repr(items)


def _seed_transactions(tmp_path: Path, rows: list[dict]) -> None:
    """Minimal cleansed.transactions slice: only what fact_card_statement reads."""
    schema = {"id": pl.Utf8, "date": pl.Date, "amount": pl.Float64, "categoryId": pl.Utf8,
              "account_type": pl.Utf8, "cc_bill_id": pl.Utf8}
    df = pl.DataFrame({c: [r.get(c) for r in rows] for c in schema}, schema=schema)
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)


_MAR = dict(id="bill-mar", dueDate="2026-03-17T00:00:00.000Z", totalAmount=349.20,
            updatedAt="2026-05-17T00:00:00.000Z",
            payments=_pay([("FULL_PAYMENT", "2026-03-17", 349.20, "DEBIT_ACCOUNT")]))
_APR = dict(id="bill-apr", dueDate="2026-04-17T00:00:00.000Z", totalAmount=731.55,
            updatedAt="2026-06-17T00:00:00.000Z",
            payments=_pay([("FULL_PAYMENT", "2026-04-17", 731.55, "DEBIT_ACCOUNT"),
                           ("OTHER_PAYMENT", "2026-03-17", 349.20, "BANK_SLIP")]))  # phantom: March's
_MAY_OLD = dict(id="bill-may-old", dueDate="2026-05-17T00:00:00.000Z", totalAmount=318.45,
                updatedAt="2026-06-15T00:00:00.000Z",
                payments=_pay([("FULL_PAYMENT", "2026-05-18", 318.45, "DEBIT_ACCOUNT")]))
_MAY_NEW = dict(id="bill-may-new", dueDate="2026-05-18T00:00:00.000Z", totalAmount=318.45,
                updatedAt="2026-07-01T00:00:00.000Z",
                payments=_pay([("OTHER_PAYMENT", "2026-04-17", 731.55, "BANK_SLIP")]))  # phantom: April's
_JUN = dict(id="bill-jun", dueDate="2026-06-17T00:00:00.000Z", totalAmount=1174.30,
            updatedAt="2026-07-01T00:00:00.000Z",
            payments=_pay([("OTHER_PAYMENT", "2026-05-18", 318.45, "BANK_SLIP")]))  # phantom: May's


def _by_id(rows: list[dict]) -> dict:
    return {r["statement_id"]: r for r in rows}


def test_phantom_prior_cycle_payment_reattributed(tmp_path):
    # April's payments[] carries March's settlement (OTHER_PAYMENT). It must NOT inflate
    # April's paid (old bug: March's + April's settlements, MIXED); March's settlement is counted once.
    _seed_bills(tmp_path, [_full_bill(**_MAR), _full_bill(**_APR)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    by = _by_id(_read_fact(tmp_path))
    assert by["bill-apr"]["paid_amount"] == 731.55
    assert by["bill-apr"]["payment_type"] == "FULL_PAYMENT"
    assert by["bill-apr"]["paid_in_full"] is True
    assert by["bill-mar"]["paid_amount"] == 349.20
    assert by["bill-mar"]["paid_in_full"] is True


def test_reissued_bill_deduped_keeps_charge_referenced_id(tmp_path):
    # Two May bills, same total, due dates 1 day apart (Pluggy re-issue). Charges reference
    # the NEW id → keep it; the settlement recorded on the dropped twin still attributes.
    _seed_bills(tmp_path, [_full_bill(**_APR), _full_bill(**_MAY_OLD), _full_bill(**_MAY_NEW)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _seed_transactions(tmp_path, [
        {"id": "c1", "date": date(2026, 5, 2), "amount": 50.0, "categoryId": "08010000",
         "account_type": "CREDIT", "cc_bill_id": "bill-may-new"},
    ])
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    may_rows = [r for r in rows if r["anomes_key"] == 202605]
    assert len(may_rows) == 1, f"202605 must have exactly one closed bill, got {len(may_rows)}"
    r = may_rows[0]
    assert r["statement_id"] == "bill-may-new"
    assert r["paid_amount"] == 318.45      # May's settlement, pooled from the dropped twin
    assert r["paid_in_full"] is True


def test_bank_leg_settles_bill_with_no_own_payment_record(tmp_path):
    # Débito automático: the June bill's own payments[] holds only May's phantom; the actual
    # settlement exists solely as a BANK 05100000 transaction (-1174.30, day after due).
    _seed_bills(tmp_path, [_full_bill(**_MAY_OLD), _full_bill(**_JUN)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _seed_transactions(tmp_path, [
        {"id": "pay-jun", "date": date(2026, 6, 18), "amount": -1174.30,
         "categoryId": "05100000", "account_type": "BANK", "cc_bill_id": None},
    ])
    _run(tmp_path)
    by = _by_id(_read_fact(tmp_path))
    assert by["bill-jun"]["paid_amount"] == 1174.30
    assert by["bill-jun"]["paid_in_full"] is True
    assert str(by["bill-jun"]["paid_date"]) == "2026-06-18"
    assert by["bill-may-old"]["paid_amount"] == 318.45   # unchanged, counted once


def test_bank_leg_and_next_bill_record_deduped(tmp_path):
    # The same settlement recorded twice — BANK leg (06-18) AND the phantom entry on the
    # next bill — must count once. Here May's settlement exists as bank leg + FULL_PAYMENT
    # on its own bill + OTHER_PAYMENT on June's bill: still 318.45, not 3x.
    _seed_bills(tmp_path, [_full_bill(**_MAY_OLD), _full_bill(**_JUN)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _seed_transactions(tmp_path, [
        {"id": "pay-may", "date": date(2026, 5, 18), "amount": -318.45,
         "categoryId": "05100000", "account_type": "BANK", "cc_bill_id": None},
    ])
    _run(tmp_path)
    by = _by_id(_read_fact(tmp_path))
    assert by["bill-may-old"]["paid_amount"] == 318.45
    assert by["bill-may-old"]["paid_in_full"] is True


def test_early_full_payment_attributes_to_own_bill(tmp_path):
    # Review finding: an exact full payment made >10d before due must match its OWN bill
    # (wide pass-1 lower bound), not fall to the pass-2 fallback and land on the previous,
    # already-settled bill (which would recreate that summed-settlements corruption class).
    early_apr = dict(id="bill-apr-early", dueDate="2026-04-17T00:00:00.000Z", totalAmount=731.55,
                     updatedAt="2026-06-17T00:00:00.000Z",
                     payments=_pay([("FULL_PAYMENT", "2026-04-02", 731.55, "DEBIT_ACCOUNT")]))
    _seed_bills(tmp_path, [_full_bill(**_MAR), _full_bill(**early_apr)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    by = _by_id(_read_fact(tmp_path))
    assert by["bill-apr-early"]["paid_amount"] == 731.55
    assert by["bill-apr-early"]["paid_in_full"] is True
    assert by["bill-mar"]["paid_amount"] == 349.20, "early Apr payment must not land on March"


def test_revised_amount_reissue_deduped(tmp_path):
    # Review finding: a re-issue that REVISED the amount must still dedup to one closed row
    # per due-month (fact_fatura_mes contract); the kept (charge-referenced) twin's amount wins.
    may_revised = dict(id="bill-may-revised", dueDate="2026-05-18T00:00:00.000Z", totalAmount=350.00,
                       updatedAt="2026-07-01T00:00:00.000Z", payments="[]")
    _seed_bills(tmp_path, [_full_bill(**_MAY_OLD), _full_bill(**may_revised)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _seed_transactions(tmp_path, [
        {"id": "c1", "date": date(2026, 5, 2), "amount": 50.0, "categoryId": "08010000",
         "account_type": "CREDIT", "cc_bill_id": "bill-may-revised"},
    ])
    _run(tmp_path)
    rows = [r for r in _read_fact(tmp_path) if r["anomes_key"] == 202605]
    assert len(rows) == 1
    assert rows[0]["statement_id"] == "bill-may-revised"
    assert rows[0]["statement_amount"] == 350.00


def test_same_bill_equal_partials_not_collapsed(tmp_path):
    # Review finding: two genuine equal partial payments recorded on the SAME bill are two
    # payments — origin-aware dedup must not collapse them (only cross-origin re-recordings).
    twin_partials = dict(id="bill-partials", dueDate="2026-04-17T00:00:00.000Z", totalAmount=200.0,
                         payments=_pay([("PARTIAL_PAYMENT", "2026-04-18", 100.0, "DEBIT_ACCOUNT"),
                                        ("PARTIAL_PAYMENT", "2026-04-20", 100.0, "DEBIT_ACCOUNT")]))
    _seed_bills(tmp_path, [_full_bill(**twin_partials)])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _run(tmp_path)
    r = _read_fact(tmp_path)[0]
    assert r["paid_amount"] == 200.0
    assert r["paid_in_full"] is True


def test_live_panorama_jun_2026(tmp_path):
    # The full scenario of df-hog: Mar..Jun incl. the May re-issue and
    # the June débito automático. Expected: 4 rows, one per month, every bill fully paid.
    _seed_bills(tmp_path, [
        _full_bill(**_MAR), _full_bill(**_APR),
        _full_bill(**_MAY_OLD), _full_bill(**_MAY_NEW), _full_bill(**_JUN),
    ])
    _seed_dim_account(tmp_path, {"acc-card": "acme-credit"})
    _seed_transactions(tmp_path, [
        {"id": "c1", "date": date(2026, 5, 2), "amount": 50.0, "categoryId": "08010000",
         "account_type": "CREDIT", "cc_bill_id": "bill-may-new"},
        {"id": "pay-jun", "date": date(2026, 6, 18), "amount": -1174.30,
         "categoryId": "05100000", "account_type": "BANK", "cc_bill_id": None},
    ])
    _run(tmp_path)
    rows = _read_fact(tmp_path)
    assert [r["anomes_key"] for r in rows] == [202603, 202604, 202605, 202606]
    expected = {
        "bill-mar": 349.20, "bill-apr": 731.55, "bill-may-new": 318.45, "bill-jun": 1174.30,
    }
    for r in rows:
        assert r["statement_id"] in expected
        assert r["paid_amount"] == expected[r["statement_id"]], r["statement_id"]
        assert r["paid_in_full"] is True, r["statement_id"]


# kept_bill_ids: the one choice of the bill that stands for a re-issued statement, read by this script and by
# raw2cleansed's copy rule (df-mk25).
def test_kept_bill_ids_prefers_referenced_then_latest_update_per_account_and_month():
    F = _delta_util.exec_functions({})
    bills = pl.DataFrame({
        "id": ["may-old", "may-new", "jun-a", "jun-b", "other-may", "undated-1", "undated-2"],
        "accountId": ["card-1", "card-1", "card-1", "card-1", "card-2", "card-1", "card-1"],
        "dueDate": ["2026-05-17T00:00:00.000Z", "2026-05-18T00:00:00.000Z", "2026-06-17T00:00:00.000Z",
                    "2026-06-17T00:00:00.000Z", "2026-05-17T00:00:00.000Z", None, "not a date"],
        "updatedAt": ["2026-07-01T00:00:00.000Z", "2026-06-01T00:00:00.000Z", "2026-07-01T00:00:00.000Z",
                      "2026-06-01T00:00:00.000Z", None, None, None],
    })
    # May: the referenced bill wins over a later update; June: nothing referenced, the latest update wins.
    assert set(F["kept_bill_ids"](bills, {"may-new"}).to_list()) == {
        "may-new", "jun-a", "other-may", "undated-1", "undated-2"}
    assert set(F["kept_bill_ids"](bills, set()).to_list()) == {
        "may-old", "jun-a", "other-may", "undated-1", "undated-2"}
