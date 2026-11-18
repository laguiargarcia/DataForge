# df-0fc: charge + estorno sharing (cc_bill_id, |amount|) with opposite txn_type annihilate.

from tests.test_fact_v3_carries import _seed_cleansed, _run_c2c, _fact


_CARD = {"accountId": "acc-card", "account_type": "CREDIT", "cc_bill_id": "bill-1"}


def _netted(tmp_path):
    return {r["id"]: r["netting_applied"] for r in _fact(tmp_path).iter_rows(named=True)}


def test_charge_and_estorno_both_flagged(tmp_path):
    # PP LIVE ROUPAS shape: charge -108.40 DEBIT (parcela 1/10) + same-bill credit +108.40.
    _seed_cleansed(tmp_path, [
        {**_CARD, "id": "charge", "date": "2026-02-10", "amount": -108.40, "txn_type": "DEBIT",
         "category": "Clothing", "categoryId": "08010000",
         "cc_installment_number": 1, "cc_total_installments": 10},
        {**_CARD, "id": "estorno", "date": "2026-02-10", "amount": 108.40, "txn_type": "CREDIT",
         "category": "Shopping", "categoryId": "08000000"},
    ])
    _run_c2c(tmp_path)
    n = _netted(tmp_path)
    assert n["charge"] is True
    assert n["estorno"] is True


def test_unpaired_charge_not_flagged(tmp_path):
    _seed_cleansed(tmp_path, [
        {**_CARD, "id": "charge", "date": "2026-02-10", "amount": -59.90, "txn_type": "DEBIT"},
    ])
    _run_c2c(tmp_path)
    assert _netted(tmp_path)["charge"] is False


def test_two_charges_one_estorno_nets_earliest_only(tmp_path):
    _seed_cleansed(tmp_path, [
        {**_CARD, "id": "charge-early", "date": "2026-02-08", "amount": -80.0, "txn_type": "DEBIT"},
        {**_CARD, "id": "charge-late", "date": "2026-02-12", "amount": -80.0, "txn_type": "DEBIT"},
        {**_CARD, "id": "estorno", "date": "2026-02-12", "amount": 80.0, "txn_type": "CREDIT"},
    ])
    _run_c2c(tmp_path)
    n = _netted(tmp_path)
    assert n["charge-early"] is True     # earliest pairs first (deterministic tie-break)
    assert n["charge-late"] is False
    assert n["estorno"] is True


def test_same_date_tie_break_by_id(tmp_path):
    # Two DEBIT charges + one estorno, all same date/bill/|amount|: k=min(2,1)=1, so only
    # one DEBIT can pair. Date ties -> the sort falls back to id, so the lexicographically
    # earlier id ("charge-a" < "charge-b") is the deterministic winner.
    _seed_cleansed(tmp_path, [
        {**_CARD, "id": "charge-a", "date": "2026-02-10", "amount": -80.0, "txn_type": "DEBIT"},
        {**_CARD, "id": "charge-b", "date": "2026-02-10", "amount": -80.0, "txn_type": "DEBIT"},
        {**_CARD, "id": "estorno", "date": "2026-02-10", "amount": 80.0, "txn_type": "CREDIT"},
    ])
    _run_c2c(tmp_path)
    n = _netted(tmp_path)
    assert n["charge-a"] is True     # id tie-break
    assert n["charge-b"] is False
    assert n["estorno"] is True


def test_payment_legs_never_participate_in_netting(tmp_path):
    # A payment leg tagged by Task 3 must not be "netted" against a same-amount charge.
    _seed_cleansed(tmp_path, [
        {**_CARD, "id": "charge", "date": "2026-06-10", "amount": -246.17, "txn_type": "DEBIT"},
        {**_CARD, "id": "pay-leg", "date": "2026-06-11", "amount": 246.17, "txn_type": "CREDIT",
         "category": "Credit card payment", "categoryId": "05100000"},
    ])
    _run_c2c(tmp_path)
    n = _netted(tmp_path)
    assert n["charge"] is False
    assert n["pay-leg"] is False


def test_different_bills_never_pair(tmp_path):
    _seed_cleansed(tmp_path, [
        {**_CARD, "id": "charge", "date": "2026-02-10", "amount": -50.0, "txn_type": "DEBIT",
         "cc_bill_id": "bill-1"},
        {**_CARD, "id": "credit-other-bill", "date": "2026-02-10", "amount": 50.0,
         "txn_type": "CREDIT", "cc_bill_id": "bill-2"},
    ])
    _run_c2c(tmp_path)
    n = _netted(tmp_path)
    assert n["charge"] is False
    assert n["credit-other-bill"] is False
