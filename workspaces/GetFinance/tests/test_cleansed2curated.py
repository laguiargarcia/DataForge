import polars as pl
import pytest
from datetime import date
from pathlib import Path

from tests import _delta_util

# Source-reading tests below read the same checkout's scripts as the run-based tests.
SCRIPTS = _delta_util.SCRIPTS

# df-s65 (A5 freeze) is now PROMOTED to prod: prod and GetFinance-Dev run byte-identical
# scripts, so curated CARRIES frozen description_normalized/etl_merchant_key in BOTH workspaces.
# The transition split (_frozen_dev_only / _derive_prod_only) is collapsed — the carry tests
# below run unconditionally. The merchant-key DERIVATION value scenarios (debit-POS prefix/ddmm
# strip, Pluggy-merchant preference, counterparty preference, whitespace collapse, config
# extensibility, A10 institution scoping) now live in test_raw2cleansed.py, where the helpers
# moved (df-s65). The cc/fatura-panel marker stays — see test_raw2cleansed.py for its status.

# spec §5 fact_transaction + df-pzu counterparty_document (FK to dim_counterparty) and
# payment_method (per-transaction attribute), plus 4 cc columns (fatura-panel spec), exact order.
FACT_COLS = [
    "id", "date", "account_uuid", "account_type", "etl_merchant_key",
    "description_normalized", "amount", "amount_signed", "category",
    "categoryId", "is_transfer", "counterparty_document", "payment_method",
    "cc_bill_id", "cc_installment_number", "cc_total_installments", "cc_purchase_date",
]
# v3 columns promoted to prod 2026-11-09 (df-668)
FACT_COLS += ["status", "txn_type", "operation_type", "cash_flag", "etl_tag", "netting_applied", "income_source_key"]


def _seed_cleansed(tmp_path: Path, transactions: list[dict] | None = None) -> None:
    """cleansed transactions Delta table on the post-df-gog transactions schema."""
    # v3 columns promoted to prod 2026-11-09 (df-668)
    rows = transactions if transactions is not None else [
        {"id": "txn-1", "date": "2026-01-15", "amount": -50.0, "category": "Food",
         "description": "Coffee Shop", "account": "Checking", "accountId": "acc-bank",
         "account_type": "BANK", "status": "POSTED", "txn_type": "DEBIT", "operation_type": "PIX"},
        {"id": "txn-2", "date": "2026-01-16", "amount": -30.0, "category": "Transport",
         "description": "Uber", "account": "Checking", "accountId": "acc-bank",
         "account_type": "BANK", "status": "POSTED", "txn_type": "DEBIT", "operation_type": "TED"},
    ]
    data_dict = {
        "id": [t.get("id") for t in rows],
        "date": [t.get("date") for t in rows],
        "amount": [t.get("amount") for t in rows],
        "category": [t.get("category") for t in rows],
        "categoryId": [t.get("categoryId") for t in rows],
        "description": [t.get("description", "") for t in rows],
        "descriptionRaw": [t.get("descriptionRaw") for t in rows],
        "merchant": [t.get("merchant") for t in rows],
        "account": [t.get("account", "Checking") for t in rows],
        "accountId": [t.get("accountId", "acc-bank") for t in rows],
        "account_type": [t.get("account_type", "BANK") for t in rows],
        "counterparty_name": [t.get("counterparty_name") for t in rows],
        "counterparty_document": [t.get("counterparty_document") for t in rows],
        "counterparty_document_type": [t.get("counterparty_document_type") for t in rows],
        "payment_method": [t.get("payment_method") for t in rows],
        "cc_bill_id": [t.get("cc_bill_id") for t in rows],
        "cc_installment_number": [t.get("cc_installment_number") for t in rows],
        "cc_total_installments": [t.get("cc_total_installments") for t in rows],
        "cc_purchase_date": [t.get("cc_purchase_date") for t in rows],
        # df-s65 (A5 freeze): these are now FROZEN inputs (derived at cleansed insert),
        # carried verbatim by curated. Tests seed them and assert the carry.
        "description_normalized": [t.get("description_normalized") for t in rows],
        "etl_merchant_key": [t.get("etl_merchant_key") for t in rows],
        "ingested_at": [t.get("ingested_at", "2026-01-01T00:00:00Z") for t in rows],
    }

    schema_dict = {
        "id": pl.Utf8, "date": pl.Utf8, "amount": pl.Float64, "category": pl.Utf8,
        "categoryId": pl.Utf8, "description": pl.Utf8, "descriptionRaw": pl.Utf8,
        "merchant": pl.Utf8, "account": pl.Utf8, "accountId": pl.Utf8,
        "account_type": pl.Utf8, "counterparty_name": pl.Utf8, "counterparty_document": pl.Utf8,
        "counterparty_document_type": pl.Utf8, "payment_method": pl.Utf8,
        "cc_bill_id": pl.Utf8, "cc_installment_number": pl.Int64,
        "cc_total_installments": pl.Int64, "cc_purchase_date": pl.Date,
        "description_normalized": pl.Utf8, "etl_merchant_key": pl.Utf8,
        "ingested_at": pl.Utf8,
    }

    # v3 columns promoted to prod 2026-11-09 (df-668)
    data_dict["status"] = [t.get("status", "POSTED") for t in rows]
    data_dict["txn_type"] = [t.get("txn_type", "DEBIT") for t in rows]
    data_dict["operation_type"] = [t.get("operation_type", "PIX") for t in rows]
    schema_dict["status"] = pl.Utf8
    schema_dict["txn_type"] = pl.Utf8
    schema_dict["operation_type"] = pl.Utf8

    df = pl.DataFrame(data_dict, schema=schema_dict).with_columns(pl.col("date").str.to_date())
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)


def _seed_dim_account(tmp_path: Path, mapping: dict | None = None) -> None:
    """mapping: pluggy_account_id -> (account_uuid, institution). dim_account shape per U3:
    account_uuid, pluggy_account_id, name, institution, type, currency."""
    if mapping is None:
        mapping = {"acc-bank": ("acme-checking", "Banco Fictício")}
    df = pl.DataFrame(
        {
            "account_uuid": [uuid for uuid, _ in mapping.values()],
            "pluggy_account_id": list(mapping.keys()),
            "name": ["n" for _ in mapping],
            "institution": [inst for _, inst in mapping.values()],
            "type": ["BANK" for _ in mapping],
            "currency": ["BRL" for _ in mapping],
        },
        schema={
            "account_uuid": pl.Utf8, "pluggy_account_id": pl.Utf8, "name": pl.Utf8,
            "institution": pl.Utf8, "type": pl.Utf8, "currency": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "curated", "dim_account", df)


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("cleansed2curated", "cleansed2curated",
                           ["--source", "cleansed", "--target", "curated"], tmp_path)


def _read_fact(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "curated", "fact_transaction").sort("id").to_dicts()


def _fact_df(tmp_path: Path) -> pl.DataFrame:
    return _delta_util.read(tmp_path, "curated", "fact_transaction")


def test_fact_transaction_writes_spec5_columns_and_types(tmp_path):
    _seed_cleansed(tmp_path)
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    df = _fact_df(tmp_path)
    assert df.columns == FACT_COLS, "fact_transaction columns must match spec §5, in order"
    schema = dict(df.schema)
    assert schema["date"] == pl.Date
    assert schema["amount"] == pl.Float64
    assert schema["amount_signed"] == pl.Float64
    assert schema["is_transfer"] == pl.Boolean
    assert schema["account_uuid"] == pl.Utf8
    assert schema["etl_merchant_key"] == pl.Utf8
    assert schema["description_normalized"] == pl.Utf8


def test_account_uuid_resolved_from_dim_account(tmp_path):
    # cleansed.transactions.accountId is the Pluggy id "acc-bank"; dim maps it to surrogate
    # "acme-checking". The fact carries the surrogate, never the raw Pluggy accountId (A1).
    _seed_cleansed(tmp_path, transactions=[
        {"id": "A", "date": "2026-01-01", "amount": 10.0, "category": "Food",
         "description": "x", "accountId": "acc-bank", "account_type": "BANK"},
    ])
    _seed_dim_account(tmp_path, {"acc-bank": ("acme-checking", "Banco Fictício")})
    _run(tmp_path)

    row = _read_fact(tmp_path)[0]
    assert row["account_uuid"] == "acme-checking", "fact carries dim surrogate, not Pluggy accountId"


def test_transaction_of_an_account_missing_from_dim_account_is_refused(tmp_path):
    # Two transactions; only one account is in dim_account. dim_account has every cleansed account
    # (D3), so the inner join dropping the other one is a lost account: the reconciliation refuses
    # the write instead of letting the report lose it (df-ahua.4.9).
    _seed_cleansed(tmp_path, transactions=[
        {"id": "KNOWN", "date": "2026-01-01", "amount": 10.0, "category": "Food",
         "description": "x", "accountId": "acc-bank", "account_type": "BANK"},
        {"id": "STRANGER", "date": "2026-01-02", "amount": 20.0, "category": "Food",
         "description": "y", "accountId": "acc-ghost", "account_type": "BANK"},
    ])
    _seed_dim_account(tmp_path, {"acc-bank": ("acme-checking", "Banco Fictício")})
    with pytest.raises(Exception, match="curated.fact_transaction vs cleansed: rows not carried") as exc:
        _run(tmp_path)
    assert type(exc.value).__name__ == "DataValidationError"
    assert not (tmp_path / "curated" / "fact_transaction").exists()


def test_is_transfer_true_for_credit_card_payment_categoryid(tmp_path):
    _seed_cleansed(tmp_path, transactions=[
        {"id": "PAY", "date": "2026-01-01", "amount": -731.55, "category": "Credit card payment",
         "categoryId": "05100000", "description": "PAGTO FATURA VISA 1111-2222",
         "accountId": "acc-bank", "account_type": "BANK"},
    ])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    row = _read_fact(tmp_path)[0]
    assert row["is_transfer"] is True


def test_is_transfer_false_for_normal_and_estorno_and_null(tmp_path):
    _seed_cleansed(tmp_path, transactions=[
        {"id": "BUY", "date": "2026-01-01", "amount": 50.0, "category": "Shopping",
         "categoryId": "08000000", "description": "Loja", "accountId": "acc-bank",
         "account_type": "CREDIT"},
        {"id": "EST", "date": "2026-01-02", "amount": -21.7, "category": "Credit card fees",
         "categoryId": "16000000", "description": "ESTORNO TARIFA EXEMPLO",
         "accountId": "acc-bank", "account_type": "CREDIT"},
        {"id": "NUL", "date": "2026-01-03", "amount": -5.0, "category": None,
         "categoryId": None, "description": "FICTPOS PADARIA  XYZ 0101",
         "accountId": "acc-bank", "account_type": "BANK"},
    ])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_fact(tmp_path)}
    assert rows["BUY"]["is_transfer"] is False
    assert rows["EST"]["is_transfer"] is False
    assert rows["NUL"]["is_transfer"] is False  # NULL categoryId must be FALSE, not NULL


def test_amount_signed_credit_negated_bank_passthrough(tmp_path):
    _seed_cleansed(tmp_path, transactions=[
        {"id": "BANK_EXP", "date": "2026-01-01", "amount": -30.0, "category": "Food",
         "description": "x", "accountId": "acc-bank", "account_type": "BANK"},
        {"id": "BANK_INC", "date": "2026-01-02", "amount": 4300.0, "category": "Income",
         "description": "y", "accountId": "acc-bank", "account_type": "BANK"},
        {"id": "CARD_BUY", "date": "2026-01-03", "amount": 50.0, "category": "Shopping",
         "description": "z", "accountId": "acc-bank", "account_type": "CREDIT"},
    ])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_fact(tmp_path)}
    assert rows["BANK_EXP"]["amount_signed"] == -30.0   # outflow stays negative
    assert rows["BANK_INC"]["amount_signed"] == 4300.0  # inflow stays positive
    assert rows["CARD_BUY"]["amount_signed"] == -50.0   # card purchase becomes an outflow


def test_carries_account_uuid_and_type(tmp_path):
    # The fact carries the resolved account_uuid (not raw accountId) + account_type.
    _seed_cleansed(tmp_path, transactions=[
        {"id": "A", "date": "2026-01-01", "amount": 10.0, "category": "Food",
         "description": "x", "accountId": "acc-card", "account_type": "CREDIT"},
    ])
    _seed_dim_account(tmp_path, {"acc-card": ("acme-credit-gold", "Banco Fictício")})
    _run(tmp_path)

    row = _read_fact(tmp_path)[0]
    assert row["account_uuid"] == "acme-credit-gold"
    assert row["account_type"] == "CREDIT"


def test_category_is_raw_pluggy_value_no_translation(tmp_path):
    # The English Pluggy category must pass through verbatim (no pt translation).
    _seed_cleansed(tmp_path, transactions=[
        {"id": "A", "date": "2026-01-01", "amount": -5.0, "category": "Food and drinks",
         "description": "x", "accountId": "acc-bank", "account_type": "BANK"},
        {"id": "B", "date": "2026-01-02", "amount": -9.0, "category": "Transport",
         "description": "y", "accountId": "acc-bank", "account_type": "BANK"},
    ])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_fact(tmp_path)}
    assert rows["A"]["category"] == "Food and drinks", "raw Pluggy category, untranslated"
    assert rows["B"]["category"] == "Transport"


def test_no_translator_import_or_network(tmp_path):
    # Guard against re-introducing GoogleTranslator: if cleansed2curated ever imports
    # deep_translator again, fail loudly. A10/no-translate decision is LOCKED.
    src = (SCRIPTS / "cleansed2curated" / "cleansed2curated.py").read_text()
    assert "deep_translator" not in src, "GoogleTranslator must not be imported (no in-ETL translation)"
    assert "GoogleTranslator" not in src
    assert "_translate" not in src, "the _translate shim must be fully removed"


def test_no_duckdb_import(tmp_path):
    # Delta port: the script must not import duckdb (the package is uninstalled in this env).
    src = (SCRIPTS / "cleansed2curated" / "cleansed2curated.py").read_text()
    assert "import duckdb" not in src, "cleansed2curated must not import duckdb (Delta port)"


# ── df-s65 (A5 freeze): curated CARRIES the frozen columns verbatim (Dev) ─────────────────
# Derivation now happens raw->cleansed; its value scenarios live in test_raw2cleansed.py. Here
# we only prove the carry is faithful: whatever cleansed froze, the fact shows unchanged.

@pytest.mark.parametrize("etl_merchant_key,description_normalized", [
    ("MERCADO FICT", "MERCADO FICT"),
    ("Padaria Real", "WHATEVER"),
    ("Pix enviado Fulano Exemplo Silva", "Pix enviado Fulano Exemplo Silva"),
    ("RESTAURANTE EXEMPLO ONLINE LTDA", "WHATEVER"),
    ("ACME", "x"),
])
def test_frozen_merchant_columns_carried_verbatim(tmp_path, etl_merchant_key, description_normalized):
    _seed_cleansed(tmp_path, transactions=[
        {"id": "A", "date": "2026-01-01", "amount": 5.0, "category": "Food",
         "description": "anything", "accountId": "acc-bank", "account_type": "BANK",
         "etl_merchant_key": etl_merchant_key, "description_normalized": description_normalized},
    ])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    row = _read_fact(tmp_path)[0]
    assert row["etl_merchant_key"] == etl_merchant_key, "curated carries frozen key unchanged"
    assert row["description_normalized"] == description_normalized, \
        "curated carries frozen description_normalized unchanged"


def test_curated_fails_fast_when_frozen_columns_absent(tmp_path):
    # A5 guard: if cleansed predates the freeze (no description_normalized/etl_merchant_key),
    # curated must NOT silently re-derive — it must raise and point at reprocess.py (df-s65).
    df = pl.DataFrame(
        {
            "id": ["A"], "date": [date(2026, 1, 1)], "amount": [5.0], "category": ["Food"],
            "categoryId": [None], "description": ["x"], "descriptionRaw": [None],
            "merchant": [None], "account": ["Checking"], "accountId": ["acc-bank"],
            "account_type": ["BANK"], "counterparty_name": [None], "counterparty_document": [None],
            "counterparty_document_type": [None], "payment_method": [None],
            "cc_bill_id": [None], "cc_installment_number": [None], "cc_total_installments": [None],
            "cc_purchase_date": [None], "ingested_at": ["2026-01-01T00:00:00Z"],
        },
        schema={
            "id": pl.Utf8, "date": pl.Date, "amount": pl.Float64, "category": pl.Utf8,
            "categoryId": pl.Utf8, "description": pl.Utf8, "descriptionRaw": pl.Utf8,
            "merchant": pl.Utf8, "account": pl.Utf8, "accountId": pl.Utf8, "account_type": pl.Utf8,
            "counterparty_name": pl.Utf8, "counterparty_document": pl.Utf8,
            "counterparty_document_type": pl.Utf8, "payment_method": pl.Utf8,
            "cc_bill_id": pl.Utf8, "cc_installment_number": pl.Int64,
            "cc_total_installments": pl.Int64, "cc_purchase_date": pl.Date, "ingested_at": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)
    _seed_dim_account(tmp_path)
    with pytest.raises(RuntimeError, match="missing frozen columns"):
        _run(tmp_path)


def test_fact_carries_counterparty_document_and_payment_method(tmp_path):
    # The fact gets counterparty_document (FK to dim_counterparty) + payment_method; the
    # counterparty NAME/TYPE live in the dim, not the fact (star schema).
    _seed_cleansed(tmp_path, transactions=[
        {"id": "BIZ", "date": "2026-01-01", "amount": -50.0, "category": "Food",
         "description": "x", "counterparty_name": "IFOOD", "counterparty_document": "11.222.333/0001-44",
         "counterparty_document_type": "PJ", "payment_method": "PIX",
         "accountId": "acc-bank", "account_type": "BANK"},
        {"id": "PLAIN", "date": "2026-01-02", "amount": -5.0, "category": "Food",
         "description": "y", "payment_method": "OTHER", "accountId": "acc-bank", "account_type": "BANK"},
    ])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_fact(tmp_path)}
    assert rows["BIZ"]["counterparty_document"] == "11.222.333/0001-44"
    assert rows["BIZ"]["payment_method"] == "PIX"
    assert rows["PLAIN"]["counterparty_document"] is None
    assert rows["PLAIN"]["payment_method"] == "OTHER"
    assert "counterparty_name" not in rows["BIZ"], "name belongs to dim_counterparty, not the fact"


# ── fatura-panel spec: 4 cc columns carried into fact_transaction ──────────────────────────


def test_cc_columns_carried_into_fact(tmp_path):
    _seed_cleansed(tmp_path, transactions=[
        {"id": "t1", "date": "2026-05-11", "amount": 21.7, "accountId": "a1",
         "account_type": "CREDIT", "cc_bill_id": "b-77", "cc_installment_number": 4,
         "cc_total_installments": 12, "cc_purchase_date": date(2025, 5, 11)},
    ])
    _seed_dim_account(tmp_path, {"a1": ("uuid-1", "Banco Fictício")})
    _run(tmp_path)
    out = _delta_util.read(tmp_path, "curated", "fact_transaction")
    row = out.row(0, named=True)
    assert row["cc_bill_id"] == "b-77"
    assert row["cc_installment_number"] == 4
    assert row["cc_total_installments"] == 12
    assert row["cc_purchase_date"] == date(2025, 5, 11)


def test_delta_fact_matches_expected_rows(tmp_path):
    # Seed the frozen merchant columns (df-s65): Dev carries them; prod derives the SAME values
    # from the descriptions ("FICTPOS MERCADO FICT0101" -> "MERCADO FICT"; "Loja" -> "Loja"), so
    # the expected rows are identical under both regimes.
    _seed_cleansed(tmp_path, transactions=[
        {"id": "A", "date": "2026-01-01", "amount": -50.0, "category": "Food and drinks",
         "description": "FICTPOS MERCADO FICT0101", "accountId": "acc-bank", "account_type": "BANK",
         "etl_merchant_key": "MERCADO FICT", "description_normalized": "MERCADO FICT"},
        {"id": "B", "date": "2026-01-02", "amount": 50.0, "category": "Shopping",
         "description": "Loja", "accountId": "acc-card", "account_type": "CREDIT",
         "etl_merchant_key": "Loja", "description_normalized": "Loja"},
    ])
    _seed_dim_account(tmp_path, {
        "acc-bank": ("acme-checking", "Banco Fictício"),
        "acc-card": ("acme-credit-gold", "Banco Fictício"),
    })
    _run(tmp_path)

    df = _fact_df(tmp_path).sort("id")
    schema = dict(df.schema)
    assert schema["date"] == pl.Date
    assert schema["amount_signed"] == pl.Float64
    assert schema["is_transfer"] == pl.Boolean
    rows = df.select(
        "id", "account_uuid", "account_type", "etl_merchant_key", "amount_signed",
        "category", "is_transfer",
    ).rows()
    assert rows == [
        ("A", "acme-checking", "BANK", "MERCADO FICT", -50.0, "Food and drinks", False),
        ("B", "acme-credit-gold", "CREDIT", "Loja", -50.0, "Shopping", False),
    ]


def test_empty_seam_produces_empty_typed_fact(tmp_path):
    # No transactions (or none mapped) => empty, correctly-typed fact, no crash.
    _seed_cleansed(tmp_path, transactions=[])
    _seed_dim_account(tmp_path)
    _run(tmp_path)

    df = _fact_df(tmp_path)
    assert len(df) == 0
    assert df.columns == FACT_COLS


def test_curated2excel_is_retired():
    assert not (SCRIPTS / "curated2excel.py").exists(), \
        "curated2excel.py is retired in U8 (Excel feed dark until U11)"


def test_finance_daily_job_has_no_export_task():
    import yaml
    job = yaml.safe_load(
        (SCRIPTS.parent / "jobs" / "finance_daily" / "job.yaml").read_text()
    )
    names = {t["name"] for t in job["tasks"]}
    assert "export" not in names, "the export task (curated2excel) is dropped in U8"
    curate = next(t for t in job["tasks"] if t["name"] == "curate")
    assert curate["script"] == "cleansed2curated/cleansed2curated.py"
    # no remaining task may depend on the removed export task
    for t in job["tasks"]:
        assert "export" not in t.get("depends_on", [])
