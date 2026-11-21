# df-668.2 (D2 default): cleaned-receiver income source key on untagged BANK inflows.
import polars as pl

from tests.test_fact_v3_carries import _seed_cleansed, _run_c2c, _fact



def _keys(tmp_path):
    return {r["id"]: r["income_source_key"] for r in _fact(tmp_path).iter_rows(named=True)}


def test_inflow_key_from_counterparty_name_whitespace_collapsed(tmp_path):
    _seed_cleansed(tmp_path, [
        {"id": "salary", "amount": 2900.0, "txn_type": "CREDIT",
         "counterparty_name": "  ACME   LTDA "},
    ])
    _run_c2c(tmp_path)
    assert _keys(tmp_path)["salary"] == "ACME LTDA"


def test_inflow_fallback_to_etl_merchant_key(tmp_path):
    _seed_cleansed(tmp_path, [
        {"id": "pix-in", "amount": 100.0, "txn_type": "CREDIT",
         "counterparty_name": None, "etl_merchant_key": "Fulano de Tal"},
    ])
    _run_c2c(tmp_path)
    assert _keys(tmp_path)["pix-in"] == "Fulano de Tal"


def test_no_key_on_outflows_card_rows_and_tagged_inflows(tmp_path):
    _seed_cleansed(tmp_path, [
        {"id": "outflow", "amount": -50.0, "txn_type": "DEBIT",
         "counterparty_name": "Mercado"},
        {"id": "card-credit", "accountId": "acc-card", "account_type": "CREDIT",
         "amount": 30.0, "txn_type": "CREDIT", "counterparty_name": "Loja"},
        {"id": "resgate", "amount": 250.0, "txn_type": "CREDIT",
         "category": "Fixed income", "categoryId": "03020000",
         "counterparty_name": "BANCO X"},  # investment-tagged inflow: NOT income
    ])
    _run_c2c(tmp_path)
    k = _keys(tmp_path)
    assert k["outflow"] is None
    assert k["card-credit"] is None
    assert k["resgate"] is None


def test_no_key_on_card_payment_bank_tagged_inflow(tmp_path):
    # card_payment_bank has no txn_type guard (intended — see cleansed2curated.py etl_tag
    # block), so this BANK/CREDIT row is tagged regardless, and income_source_key requires
    # etl_tag.is_null() — the tag alone is enough to suppress the key.
    _seed_cleansed(tmp_path, [
        {"id": "cc-payment", "amount": 246.17, "txn_type": "CREDIT", "operation_type": "OUTROS",
         "category": "Credit card payment", "categoryId": "05100000",
         "counterparty_name": "BANCO"},
    ])
    _run_c2c(tmp_path)
    fact = _fact(tmp_path)
    row = fact.filter(pl.col("id") == "cc-payment").to_dicts()[0]
    assert row["etl_tag"] == "card_payment_bank"
    assert row["income_source_key"] is None
