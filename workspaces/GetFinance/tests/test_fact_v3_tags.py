# df-dnc + E2b: structural etl_tag — investment ops and both bill-payment legs.

from tests.test_fact_v3_carries import _seed_cleansed, _run_c2c, _fact



def _tags(tmp_path):
    return {r["id"]: r["etl_tag"] for r in _fact(tmp_path).iter_rows(named=True)}


def test_investment_tag_by_category_prefix_and_resgate_op(tmp_path):
    _seed_cleansed(tmp_path, [
        {"id": "apl", "amount": -250.0, "category": "Fixed income", "categoryId": "03020000",
         "txn_type": "DEBIT", "operation_type": "OUTROS"},                     # aplicação (op is useless)
        {"id": "res", "amount": 250.0, "category": "Fixed income", "categoryId": "03020000",
         "txn_type": "CREDIT", "operation_type": "RESGATE_APLIC_FINANCEIRA"},  # resgate
        {"id": "res-badcat", "amount": 100.0, "category": "Other", "categoryId": "99000000",
         "txn_type": "CREDIT", "operation_type": "RESGATE_APLIC_FINANCEIRA"},  # op rescues miscategorization
        {"id": "food", "amount": -20.0, "category": "Groceries", "categoryId": "19000000"},
    ])
    _run_c2c(tmp_path)
    t = _tags(tmp_path)
    assert t["apl"] == "investment"
    assert t["res"] == "investment"
    assert t["res-badcat"] == "investment"
    assert t["food"] is None


def test_bill_payment_both_legs_category_shape(tmp_path):
    # Shape 1: both legs carry categoryId 05100000; card leg amount is NEGATIVE with type CREDIT.
    _seed_cleansed(tmp_path, [
        {"id": "bank-leg", "amount": -187.90, "category": "Credit card payment",
         "categoryId": "05100000", "account_type": "BANK", "txn_type": "DEBIT"},
        {"id": "card-leg", "accountId": "acc-card", "account_type": "CREDIT",
         "amount": -187.90, "category": "Credit card payment", "categoryId": "05100000",
         "description_normalized": "Pagamento recebido", "txn_type": "CREDIT"},
    ])
    _run_c2c(tmp_path)
    t = _tags(tmp_path)
    assert t["bank-leg"] == "card_payment_bank"
    assert t["card-leg"] == "card_payment_card"


def test_bill_payment_card_leg_mislabeled_shape_mirror_match(tmp_path):
    # Shape 2 (Finding B): card leg is mislabeled Shopping/08000000 — only the BANK mirror
    # (equal |amount|, 05100000, within 3 days) identifies it.
    _seed_cleansed(tmp_path, [
        {"id": "bank-leg", "date": "2026-06-10", "amount": -246.17,
         "category": "Credit card payment", "categoryId": "05100000",
         "account_type": "BANK", "txn_type": "DEBIT"},
        {"id": "card-leg", "date": "2026-06-11", "accountId": "acc-card",
         "account_type": "CREDIT", "amount": 246.17, "category": "Shopping",
         "categoryId": "08000000", "description_normalized": "PAGAMENTO ON LINE",
         "txn_type": "CREDIT"},
    ])
    _run_c2c(tmp_path)
    t = _tags(tmp_path)
    assert t["bank-leg"] == "card_payment_bank"
    assert t["card-leg"] == "card_payment_card"


def test_genuine_card_refund_not_tagged_as_payment(tmp_path):
    # A CREDIT-account inflow with NO bank mirror and non-payment category stays untagged
    # (it is estorno material for df-0fc netting, not a payment leg).
    _seed_cleansed(tmp_path, [
        {"id": "refund", "date": "2026-06-12", "accountId": "acc-card",
         "account_type": "CREDIT", "amount": 108.40, "category": "Shopping",
         "categoryId": "08000000", "txn_type": "CREDIT"},
    ])
    _run_c2c(tmp_path)
    assert _tags(tmp_path)["refund"] is None


def test_bill_payment_by_automatic_debit_shape(tmp_path):
    # Shape 3 (df-sf61): since 07/2026 the card leg of the automatic debit is "Transfer - Internal"
    # (05060000) and its BANK leg has no stable category (Investments, none, Transfer - Internal).
    card_leg = {"accountId": "acc-card", "account_type": "CREDIT", "category": "Transfer - Internal",
                "categoryId": "05060000", "description_normalized": "PGTO FATURA AUTOM",
                "txn_type": "CREDIT", "operation_type": "OUTROS"}
    bank_leg = {"account_type": "BANK", "txn_type": "DEBIT", "operation_type": "OUTROS"}
    _seed_cleansed(tmp_path, [
        {**card_leg, "id": "card-jul", "date": "2026-07-17", "amount": -321.09},
        {**bank_leg, "id": "bank-jul", "date": "2026-07-17", "amount": -321.09,
         "category": "Investments", "categoryId": "03000000"},
        {**card_leg, "id": "card-sep", "date": "2026-09-17", "amount": -98.76},
        {**bank_leg, "id": "bank-sep", "date": "2026-09-18", "amount": -98.76,
         "category": None, "categoryId": None},
        {**card_leg, "id": "card-oct", "date": "2026-10-19", "amount": -45.67},
        {**bank_leg, "id": "bank-oct", "date": "2026-10-16", "amount": -45.67,
         "category": "Transfer - Internal", "categoryId": "05060000"},
    ])
    _run_c2c(tmp_path)
    t = _tags(tmp_path)
    for month in ("jul", "sep", "oct"):
        assert t[f"card-{month}"] == "card_payment_card"
        assert t[f"bank-{month}"] == "card_payment_bank"


def test_bank_debit_off_the_payment_stays_as_it_was(tmp_path):
    # The bank mirror needs the same |amount| within 3 days of a card payment leg: an investment
    # a day too far, a debit of another amount and an internal-transfer inflow keep their tag.
    _seed_cleansed(tmp_path, [
        {"id": "card-leg", "date": "2026-08-17", "accountId": "acc-card", "account_type": "CREDIT",
         "amount": -500.0, "category": "Transfer - Internal", "categoryId": "05060000",
         "txn_type": "CREDIT"},
        {"id": "apl-far", "date": "2026-08-21", "account_type": "BANK", "amount": -500.0,
         "category": "Investments", "categoryId": "03000000", "txn_type": "DEBIT"},
        {"id": "other-amount", "date": "2026-08-17", "account_type": "BANK", "amount": -500.01,
         "category": "Transfer - Internal", "categoryId": "05060000", "txn_type": "DEBIT"},
        {"id": "inflow", "date": "2026-08-17", "account_type": "BANK", "amount": 500.0,
         "category": "Transfer - Internal", "categoryId": "05060000", "txn_type": "CREDIT"},
    ])
    _run_c2c(tmp_path)
    t = _tags(tmp_path)
    assert t["card-leg"] == "card_payment_card"
    assert t["apl-far"] == "investment"
    assert t["other-amount"] is None
    assert t["inflow"] is None
