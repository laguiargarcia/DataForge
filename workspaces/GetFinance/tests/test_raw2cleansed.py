import json

import polars as pl
import pytest
from datetime import date
from pathlib import Path

from tests import _delta_util

# Guard: tests that assert columns added in the fatura-panel spec are Dev-workspace only until
# the changes are promoted to prod via 'dataforge deploy apply'.
# df-s65 (A5 freeze) is now PROMOTED to prod: raw2cleansed derives + freezes
# description_normalized/etl_merchant_key identically in BOTH workspaces, so the frozen-
# derivation + A10 tests below run UNCONDITIONALLY (the transition gate is retired).

TXN_COLS = ["id", "date", "amount", "category", "categoryId", "description",
            "descriptionRaw", "merchant", "accountId", "accountName", "type", "paymentData",
            "creditCardMetadata", "ingested_at"]
ACC_COLS = ["id", "type", "name", "ingested_at"]


def _write_partition(raw_root: Path, entity: str, records: list[dict],
                     cols: list[str], ingestion_date: str) -> None:
    """Write one Parquet partition mirroring write_parquet_partition output (all VARCHAR)."""
    part_dir = raw_root / entity / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / "data.parquet"
    data = {c: [(str(r.get(c)) if r.get(c) is not None else None) for r in records] for c in cols}
    pl.DataFrame(data, schema={c: pl.Utf8 for c in cols}).write_parquet(str(out))


def _seed_raw(raw_root: Path, transactions: list[dict] | None = None,
              accounts: list[dict] | None = None, ingestion_date: str = "2026-01-01",
              extra_cols: tuple = ()) -> None:
    """Write a raw Parquet partition with the post-landing2raw transaction + account shape.

    Mirrors the live raw/<entity>/ingestion_date=*/data.parquet partitions the cleansing
    step reads, plus a raw.accounts partition so account_type can be joined in.
    """
    rows = transactions if transactions is not None else [
        {"id": "txn-1", "date": "2026-01-15", "amount": "-50.0", "category": "Food",
         "description": "Coffee", "accountId": "acc-1", "accountName": "Checking",
         "ingested_at": "2026-01-01T10:00:00Z"},
        {"id": "txn-1", "date": "2026-01-15", "amount": "-50.0", "category": "Food",
         "description": "Coffee", "accountId": "acc-1", "accountName": "Checking",
         "ingested_at": "2026-01-01T11:00:00Z"},
        {"id": "txn-2", "date": "2026-01-16", "amount": "-30.0", "category": "Transport",
         "description": "Uber", "accountId": "acc-1", "accountName": "Checking",
         "ingested_at": "2026-01-01T09:00:00Z"},
    ]
    accs = accounts if accounts is not None else [{"id": "acc-1", "type": "BANK", "name": "Checking"}]
    acc_type = {a["id"]: a["type"] for a in accs}

    def _default_type(t):
        # Pluggy's sign convention, which raw2cleansed now enforces (df-ahua.3): on a BANK account
        # money in (CREDIT) is positive; on a CREDIT card a charge (DEBIT) is positive.
        positive = float(t.get("amount", "0")) > 0
        on_bank = acc_type.get(t.get("accountId", "acc-1"), "BANK") == "BANK"
        return "CREDIT" if positive == on_bank else "DEBIT"

    txn_records = [
        {
            "id": t["id"], "date": t["date"], "amount": str(t.get("amount", "0")),
            "category": t.get("category", ""), "categoryId": t.get("categoryId", ""),
            "description": t.get("description", ""), "descriptionRaw": t.get("descriptionRaw", ""),
            "merchant": t.get("merchant"), "accountId": t.get("accountId", "acc-1"),
            "accountName": t.get("accountName", "Checking"),
            "type": t.get("type", _default_type(t)), "paymentData": t.get("paymentData"),
            "creditCardMetadata": t.get("creditCardMetadata"),
            "ingested_at": t.get("ingested_at", f"{ingestion_date}T00:00:00Z"),
            **{c: t.get(c) for c in extra_cols},
        }
        for t in rows
    ]
    _write_partition(raw_root, "transactions", txn_records, TXN_COLS + list(extra_cols), ingestion_date)

    acc_records = [
        {"id": a["id"], "type": a["type"], "name": a.get("name", ""),
         "ingested_at": a.get("ingested_at", f"{ingestion_date}T00:00:00Z")}
        for a in accs
    ]
    _write_partition(raw_root, "accounts", acc_records, ACC_COLS, ingestion_date)


def _read_cleansed(tmp_path: Path) -> list[dict]:
    return _delta_util.read(tmp_path, "cleansed", "transactions").sort("id").to_dicts()


def _run(tmp_path: Path) -> None:
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)


def test_raw2cleansed_deduplicates_by_id(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)

    rows = _read_cleansed(tmp_path)
    assert len(rows) == 2
    assert rows[0]["id"] == "txn-1"


def test_raw2cleansed_casts_amount_to_double(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)

    df = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert df.schema["amount"] == pl.Float64


def test_date_cast_to_date(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert df.schema["date"] == pl.Date


def test_date_cast_from_iso_timestamp(tmp_path):
    # Live Pluggy `date` is a full ISO timestamp (e.g. "2026-06-03T15:12:50.072Z"), not a
    # plain YYYY-MM-DD. It must truncate to the date (mirrors DuckDB CAST(... AS DATE)).
    import datetime
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "txn-ts", "date": "2026-06-03T15:12:50.072Z", "amount": "-12.0"},
    ])
    _run(tmp_path)
    df = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert df.schema["date"] == pl.Date
    row = df.filter(pl.col("id") == "txn-ts").to_dicts()[0]
    assert row["date"] == datetime.date(2026, 6, 3)


def test_first_run_carries_all_columns(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "A", "date": "2026-01-01", "amount": "100.0", "accountId": "acc-1",
         "categoryId": "cat-1", "descriptionRaw": "RAW DESC", "merchant": "ACME"},
    ], accounts=[{"id": "acc-1", "type": "BANK", "name": "Checking"}])
    _run(tmp_path)

    rows = _read_cleansed(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    for col in ("accountId", "account_type", "categoryId", "descriptionRaw", "merchant"):
        assert col in row, f"missing column {col}"
    assert row["account_type"] == "BANK"
    assert row["accountId"] == "acc-1"


def test_output_columns_exact(tmp_path):
    # Single canonical cleansed.transactions schema: df-pzu counterparty cols + 4 cc cols
    # (fatura-panel) + the df-s65 frozen description_normalized/etl_merchant_key, in order.
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)
    cols = _delta_util.read(tmp_path, "cleansed", "transactions").columns
    expected_cols = [
        "id", "date", "amount", "category", "categoryId", "description",
        "descriptionRaw", "merchant", "account", "accountId", "account_type",
        "counterparty_name", "counterparty_document", "counterparty_document_type",
        "payment_method",
        "cc_bill_id", "cc_installment_number", "cc_total_installments", "cc_purchase_date",
        "description_normalized", "etl_merchant_key",
    ]
    # v3 columns promoted to prod 2026-11-09 (df-668)
    expected_cols += ["status", "txn_type", "operation_type"]
    expected_cols.append("ingested_at")
    assert cols == expected_cols


def test_accumulation_refreshes_and_preserves(tmp_path):
    # Run 1: raw window has A, B (partition).
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "A", "date": "2026-01-01", "amount": "10.0", "ingested_at": "2026-01-01T00:00:00Z"},
        {"id": "B", "date": "2026-01-02", "amount": "20.0", "ingested_at": "2026-01-01T00:00:00Z"},
    ], ingestion_date="2026-01-01")
    _run(tmp_path)
    assert {r["id"] for r in _read_cleansed(tmp_path)} == {"A", "B"}

    # Run 2: SAME-day re-fetch OVERWRITES the partition — A has aged out of the Pluggy window,
    # raw now returns only B (changed) and C (new). A survives solely via the cleansed-accumulate
    # MERGE (non-matched target rows are untouched). B's amount is updated; C is inserted.
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "B", "date": "2026-01-02", "amount": "999.0", "ingested_at": "2026-01-01T12:00:00Z"},
        {"id": "C", "date": "2026-01-03", "amount": "30.0", "ingested_at": "2026-01-01T12:00:00Z"},
    ], ingestion_date="2026-01-01")
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_cleansed(tmp_path)}
    assert set(rows) == {"A", "B", "C"}, f"final table must be A,B,C; got {set(rows)}"
    assert rows["A"]["amount"] == 10.0, "A (aged out of raw) must persist with original value"
    assert rows["B"]["amount"] == 999.0, "B must be refreshed to the updated value"
    assert rows["C"]["amount"] == 30.0, "C must be inserted"


def test_account_type_joined_from_raw_accounts(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "T1", "date": "2026-01-01", "amount": "50.0", "accountId": "acc_bank"},
        {"id": "T2", "date": "2026-01-02", "amount": "80.0", "accountId": "acc_card"},
    ], accounts=[
        {"id": "acc_bank", "type": "BANK", "name": "Checking"},
        {"id": "acc_card", "type": "CREDIT", "name": "Card"},
    ])
    _run(tmp_path)

    rows = {r["id"]: r for r in _read_cleansed(tmp_path)}
    assert rows["T1"]["account_type"] == "BANK"
    assert rows["T2"]["account_type"] == "CREDIT"


def test_empty_glob_is_noop_preserving_prior_rows(tmp_path):
    # No raw transactions partition → the glob matches nothing. Transactions ACCUMULATE, so an
    # empty fetch must be a NO-OP (pl.concat([]) would crash; an empty overwrite would wipe
    # history — A11). A pre-seeded cleansed.transactions row must survive intact.
    import datetime
    prior = pl.DataFrame(
        {
            "id": ["keep-me"],
            "date": [datetime.date(2026, 1, 1)],
            "amount": [42.0],
            "category": ["Food"], "categoryId": ["c1"], "description": ["d"],
            "descriptionRaw": ["dr"], "merchant": ["m"], "account": ["Checking"],
            "accountId": ["acc-1"], "account_type": ["BANK"],
            "ingested_at": ["2026-01-01T00:00:00Z"],
        },
        schema={
            "id": pl.Utf8, "date": pl.Date, "amount": pl.Float64, "category": pl.Utf8,
            "categoryId": pl.Utf8, "description": pl.Utf8, "descriptionRaw": pl.Utf8,
            "merchant": pl.Utf8, "account": pl.Utf8, "accountId": pl.Utf8,
            "account_type": pl.Utf8, "ingested_at": pl.Utf8,
        },
    )
    _delta_util.seed(tmp_path, "cleansed", "transactions", prior)

    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    _run(tmp_path)  # must not crash on the empty glob

    rows = _read_cleansed(tmp_path)
    assert len(rows) == 1, "empty fetch must be a no-op; prior cleansed row must survive"
    assert rows[0]["id"] == "keep-me"
    assert rows[0]["amount"] == 42.0


def test_no_exported_at_column(tmp_path):
    _seed_raw(tmp_path / "raw")
    _run(tmp_path)
    cols = set(_delta_util.read(tmp_path, "cleansed", "transactions").columns)
    assert "exported_at" not in cols, "exported_at is retired (its only consumer curated2excel is gone)"


# ── df-ahua.3.1: a purchase in another currency ──────────────────────────────────────────
# Pluggy sends the foreign value in `amount`, its currency in currencyCode and the BRL value the bank
# converted in amountInAccountCurrency (null on BRL rows). cleansed keeps the BRL value.
CURRENCY_COLS = ("currencyCode", "amountInAccountCurrency")


def _card(id_, amount, currency="BRL", converted=None, **more):
    return {"id": id_, "date": "2026-01-10", "amount": amount, "accountId": "acc-card",
            "accountName": "Card", "currencyCode": currency, "amountInAccountCurrency": converted, **more}


CARD = [{"id": "acc-card", "type": "CREDIT", "name": "Card"}]


def _amounts(tmp_path):
    return {r["id"]: r["amount"] for r in _read_cleansed(tmp_path)}


def _findings(tmp_path):
    path = tmp_path / "validation" / "raw.transactions.json"
    return [f["rule"] for f in json.loads(path.read_text(encoding="utf-8"))["findings"]]


FOREIGN_UNCONVERTED = "currencyCode is not BRL and amountInAccountCurrency is null"


def test_brl_row_keeps_its_amount_and_foreign_row_takes_the_converted_one(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("brl", "35.9"), _card("usd", "10.0", "USD", "52.5")],
              accounts=CARD, extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"brl": 35.9, "usd": 52.5}
    assert FOREIGN_UNCONVERTED not in _findings(tmp_path)


def test_foreign_row_without_the_converted_amount_warns_and_keeps_amount(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("usd", "10.0", "USD", None)],
              accounts=CARD, extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"usd": 10.0}
    assert FOREIGN_UNCONVERTED in _findings(tmp_path)


# Partitions older than the currency fields: in the combined raw with a newer one (diagonal concat
# fills the column with null), and alone (the script projects the absent column as null).
def test_partition_without_the_currency_columns(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("old", "20.0")], accounts=CARD,
              ingestion_date="2026-01-01")
    _seed_raw(tmp_path / "raw", transactions=[_card("new", "10.0", "USD", "52.5")], accounts=CARD,
              ingestion_date="2026-01-02", extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"old": 20.0, "new": 52.5}


def test_only_partitions_without_the_currency_columns(tmp_path):
    _seed_raw(tmp_path / "raw", transactions=[_card("old", "20.0")], accounts=CARD)
    _run(tmp_path)
    assert _amounts(tmp_path) == {"old": 20.0}
    assert FOREIGN_UNCONVERTED not in _findings(tmp_path)


# How prod is corrected: cleansed.transactions already holds the foreign value for an id (written
# before df-ahua.3.1); the next run's MERGE updates the matched row with the converted value and leaves
# the other rows as they were.
def test_merge_corrects_a_row_cleansed_holds_with_the_foreign_amount(tmp_path):
    from deltalake import DeltaTable
    _seed_raw(tmp_path / "raw", transactions=[_card("brl", "35.9"), _card("usd", "10.0", "USD", "52.5")],
              accounts=CARD, extra_cols=CURRENCY_COLS)
    _run(tmp_path)
    table = tmp_path / "cleansed" / "transactions"
    DeltaTable(str(table)).update(predicate="id = 'usd'", updates={"amount": "10.0"})
    before = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    assert before["amount"].to_list() == [35.9, 10.0]

    _run(tmp_path)
    after = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    assert after["amount"].to_list() == [35.9, 52.5]
    assert after.drop("amount").equals(before.drop("amount"))
    assert DeltaTable(str(table)).history(1)[0]["operation"] == "MERGE"


# ── df-pzu: paymentData -> 4 frozen counterparty columns ──────────────────────────────────
# Counterparty = the side OPPOSITE our account: receiver on a DEBIT (we paid), payer on a
# CREDIT (we were paid). NO fallback to the other side
# (the other side is our own account holder).

def _cp_row(tmp_path, txn_type, payment_data):
    _seed_raw(tmp_path / "raw", transactions=[
        {"id": "T", "date": "2026-01-01", "amount": "-5.0" if txn_type == "DEBIT" else "5.0",
         "type": txn_type,
         "paymentData": payment_data, "accountId": "acc-1"},
    ], accounts=[{"id": "acc-1", "type": "BANK"}])
    _run(tmp_path)
    return _read_cleansed(tmp_path)[0]


def test_paymentdata_debit_uses_receiver(tmp_path):
    row = _cp_row(tmp_path, "DEBIT", {
        "paymentMethod": "PIX",
        "receiver": {"name": "IFOOD", "documentNumber": {"type": "CNPJ", "value": "11.222.333/0001-44"}},
        "payer": {"name": None, "documentNumber": {"type": "CPF", "value": "314.926.589-32"}},
    })
    assert row["counterparty_name"] == "IFOOD"
    assert row["counterparty_document"] == "11.222.333/0001-44"
    assert row["counterparty_document_type"] == "PJ"
    assert row["payment_method"] == "PIX"


def test_paymentdata_credit_uses_payer(tmp_path):
    row = _cp_row(tmp_path, "CREDIT", {
        "paymentMethod": "TED",
        "receiver": {"name": None, "documentNumber": {"type": "CPF", "value": "314.926.589-32"}},
        "payer": {"name": "CONTRAPARTE EXEMPLO", "documentNumber": {"type": "CNPJ", "value": "11.445.778/0001-99"}},
    })
    assert row["counterparty_name"] == "CONTRAPARTE EXEMPLO"
    assert row["counterparty_document"] == "11.445.778/0001-99"
    assert row["counterparty_document_type"] == "PJ"
    assert row["payment_method"] == "TED"


def test_paymentdata_cpf_maps_to_pf_name_may_be_null(tmp_path):
    # Person counterparties carry a CPF but Pluggy exposes no name (PII) — name stays null.
    row = _cp_row(tmp_path, "DEBIT", {
        "paymentMethod": "PIX",
        "receiver": {"name": None, "documentNumber": {"type": "CPF", "value": "626.383.950-12"}},
    })
    assert row["counterparty_document"] == "626.383.950-12"
    assert row["counterparty_document_type"] == "PF"
    assert row["counterparty_name"] is None


def test_paymentdata_no_fallback_to_account_holder(tmp_path):
    # DEBIT with the chosen side (receiver) absent: must NOT fall back to payer (our own
    # account holder). The locked acceptance counts of counterparty CPFs depend on this.
    row = _cp_row(tmp_path, "DEBIT", {
        "paymentMethod": "OTHER",
        "payer": {"name": None, "documentNumber": {"type": "CPF", "value": "314.926.589-32"}},
    })
    assert row["counterparty_document"] is None, "must not borrow the account holder's own document"
    assert row["counterparty_name"] is None
    assert row["counterparty_document_type"] is None
    assert row["payment_method"] == "OTHER"


def test_paymentdata_none_or_malformed_yields_all_null(tmp_path):
    none_row = _cp_row(tmp_path, "DEBIT", None)  # stored as the literal "None" / null
    for col in ("counterparty_name", "counterparty_document", "counterparty_document_type", "payment_method"):
        assert none_row[col] is None

    bad_row = _cp_row(tmp_path, "DEBIT", "{not a dict")  # malformed repr
    for col in ("counterparty_name", "counterparty_document", "counterparty_document_type", "payment_method"):
        assert bad_row[col] is None


def test_payment_method_extracted_without_counterparty(tmp_path):
    # paymentMethod is a per-transaction attribute populated even when no counterparty doc exists.
    row = _cp_row(tmp_path, "DEBIT", {"paymentMethod": "DEBIT", "receiver": None, "payer": None})
    assert row["payment_method"] == "DEBIT"
    assert row["counterparty_document"] is None


# ── fatura-panel spec: creditCardMetadata -> 4 frozen cc columns ──────────────────────────


def test_cc_metadata_parsed_to_four_columns(tmp_path):
    _seed_raw(tmp_path / "raw", [
        {"id": "t1", "date": "2026-05-19", "amount": "463.85", "accountId": "a1",
         "creditCardMetadata": "{'cardNumber': '7781', 'billId': 'b-123'}"},
        {"id": "t2", "date": "2025-05-11", "amount": "21.7", "accountId": "a1",
         "creditCardMetadata": ("{'cardNumber': '4821', 'purchaseDate': '2025-05-11T03:00:00.000Z', "
                                "'totalInstallments': 12, 'installmentNumber': 4, 'billId': 'b-77'}")},
    ], accounts=[{"id": "a1", "type": "CREDIT"}])
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions").sort("id")
    assert out.get_column("cc_bill_id").to_list() == ["b-123", "b-77"]
    assert out.get_column("cc_installment_number").to_list() == [None, 4]
    assert out.get_column("cc_total_installments").to_list() == [None, 12]
    assert out.get_column("cc_purchase_date").to_list() == [None, date(2025, 5, 11)]


def test_cc_metadata_null_none_or_malformed_yields_all_null(tmp_path):
    _seed_raw(tmp_path / "raw", [
        {"id": "t1", "date": "2026-05-01", "amount": "1.0", "accountId": "a1",
         "creditCardMetadata": None},
        {"id": "t2", "date": "2026-05-02", "amount": "2.0", "accountId": "a1",
         "creditCardMetadata": "None"},
        {"id": "t3", "date": "2026-05-03", "amount": "3.0", "accountId": "a1",
         "creditCardMetadata": "{broken"},
    ], accounts=[{"id": "a1", "type": "CREDIT"}])
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions")
    for col in ("cc_bill_id", "cc_installment_number", "cc_total_installments", "cc_purchase_date"):
        assert out.get_column(col).null_count() == 3


def test_cc_metadata_column_absent_from_raw_projects_null(tmp_path):
    # Simulates reprocess over partitions that predate creditCardMetadata capture:
    # seed WITHOUT the column (build the partition with the pre-change TXN_COLS).
    cols_without = [c for c in TXN_COLS if c != "creditCardMetadata"]
    _write_partition(tmp_path / "raw", "transactions",
                     [{"id": "t1", "date": "2026-05-01", "amount": "1.0", "accountId": "a1",
                       "ingested_at": "2026-01-01"}],
                     cols_without, "2026-01-01")
    _write_partition(tmp_path / "raw", "accounts",
                     [{"id": "a1", "type": "CREDIT", "ingested_at": "2026-01-01"}], ACC_COLS,
                     "2026-01-01")
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], tmp_path)
    out = _delta_util.read(tmp_path, "cleansed", "transactions")
    assert out.get_column("cc_bill_id").to_list() == [None]


# ── df-s65 (A5 freeze) + df-7sa: description_normalized + etl_merchant_key frozen at cleansed ──
# Derivation moved raw->cleansed (was curated). These PORT the value scenarios that used to
# live in test_cleansed2curated.py. Frozen with institution=None (match-all) — identical output
# to the old curated derivation under today's un-scoped config.

def _frozen_row(tmp_path, **txn):
    txn.setdefault("id", "A")
    txn.setdefault("date", "2026-01-01")
    txn.setdefault("amount", "5.0")
    txn.setdefault("accountId", "acc-1")
    _seed_raw(tmp_path / "raw", transactions=[txn],
              accounts=[{"id": "acc-1", "type": "BANK"}])
    _run(tmp_path)
    return _read_cleansed(tmp_path)[0]


@pytest.mark.parametrize("description,expected", [
    ("FICTPOS MERCADO FICT0101", "MERCADO FICT"),
    ("FICTPOS PADARIA  XYZ 0101", "PADARIA XYZ"),
    ("FICTPOS SORVETERIA X0202", "SORVETERIA X"),
    ("FICTPOS FARMACIA EXE0202", "FARMACIA EXE"),
])
def test_frozen_etl_merchant_key_debit_pos_rule_strips_prefix_and_ddmm(tmp_path, description, expected):
    # Uses the shipped config (example-debit-pos-prefix rule, no institution: => match-all). With no
    # Pluggy merchant, the key IS the normalized description.
    row = _frozen_row(tmp_path, description=description)
    assert row["etl_merchant_key"] == expected
    assert row["description_normalized"] == expected


def test_frozen_etl_merchant_key_prefers_plain_pluggy_merchant(tmp_path):
    # A plain-string Pluggy merchant wins; description_normalized is still from the description.
    row = _frozen_row(tmp_path, description="FICTPOS WHATEVER  0101", merchant="Padaria Real")
    assert row["etl_merchant_key"] == "Padaria Real"
    assert row["description_normalized"] == "WHATEVER"


def test_frozen_etl_merchant_key_prefers_counterparty_name_over_description(tmp_path):
    # No Pluggy merchant: the frozen counterparty_name (from paymentData) becomes the key.
    row = _frozen_row(tmp_path, description="FICTPOS WHATEVER  0101", merchant=None,
                      type="DEBIT", amount="-5.0", paymentData={
                          "paymentMethod": "PIX",
                          "receiver": {"name": "RESTAURANTE EXEMPLO ONLINE LTDA",
                                       "documentNumber": {"type": "CNPJ", "value": "11.222.333/0001-44"}},
                      })
    assert row["counterparty_name"] == "RESTAURANTE EXEMPLO ONLINE LTDA"
    assert row["etl_merchant_key"] == "RESTAURANTE EXEMPLO ONLINE LTDA"
    assert row["description_normalized"] == "WHATEVER", "normalized string still from description"


def test_frozen_etl_merchant_key_unpacks_nested_merchant_repr_string(tmp_path):
    # df-7sa: landing str()-ifies a nested Pluggy merchant object into a repr-string. The
    # frozen key must extract the merchant NAME (preferred over businessName), not the repr.
    row = _frozen_row(tmp_path, description="x",
                      merchant="{'name': 'ACME', 'businessName': 'ACME LTDA'}")
    assert row["etl_merchant_key"] == "ACME", "name preferred over businessName; repr-string unpacked"


def test_frozen_etl_merchant_key_empty_merchant_falls_through_to_description(tmp_path):
    # M5: merchant == "" (empty string) must fall through to description_normalized.
    row = _frozen_row(tmp_path, description="FICTPOS MERCADO FICT0101", merchant="")
    assert row["etl_merchant_key"] == "MERCADO FICT"
    assert row["description_normalized"] == "MERCADO FICT"


def test_frozen_description_normalized_collapses_whitespace_for_unmatched(tmp_path):
    row = _frozen_row(tmp_path, description="Pix enviado   Fulano  Exemplo Silva")
    assert row["etl_merchant_key"] == "Pix enviado Fulano Exemplo Silva"
    assert row["description_normalized"] == "Pix enviado Fulano Exemplo Silva"


def test_frozen_etl_merchant_key_prefers_merchant_over_counterparty_name(tmp_path):
    # Precedence (df-s65, ported from test_cleansed2curated): when BOTH a structured Pluggy
    # merchant AND a counterparty_name are present, the merchant wins (not just merchant-vs-
    # description). Pairs with the merchant-vs-description and counterparty-vs-description cases.
    row = _frozen_row(tmp_path, description="FICTPOS WHATEVER  0101", merchant="Padaria Real",
                      type="DEBIT", amount="-5.0", paymentData={
                          "paymentMethod": "PIX",
                          "receiver": {"name": "IFOOD",
                                       "documentNumber": {"type": "CNPJ", "value": "11.222.333/0001-44"}},
                      })
    assert row["counterparty_name"] == "IFOOD"
    assert row["etl_merchant_key"] == "Padaria Real", "structured merchant beats counterparty_name"


def test_frozen_etl_merchant_key_extensible_to_another_bank_via_config(tmp_path, monkeypatch):
    # Config-driven multi-institution support (df-s65, ported): a different bank's description
    # format normalizes with only a config change, no code change. (Rule un-scoped => match-all.)
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(
        "rules:\n"
        "  - name: bankx\n"
        "    match: '^TXN '\n"
        "    strip:\n"
        "      - '^TXN '\n"
        "      - ' #\\d+$'\n"
    )
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    row = _frozen_row(tmp_path, description="TXN PADARIA CENTRAL #0042")
    assert row["etl_merchant_key"] == "PADARIA CENTRAL"


# ── df-bsb2.3: a bank's own rules live in an optional, gitignored local file ───────────────
# Beside the rules file (default config/merchant_normalization.yaml, or MERCHANT_NORM_CONFIG)
# the script reads "<name>.local.yaml" when it exists, and tries its rules first.

_VERSIONED_STRIPS_PREFIX = (
    "rules:\n"
    "  - name: versioned\n"
    "    match: '^LOCALPFX '\n"
    "    strip:\n"
    "      - '^LOCALPFX '\n"
    "  - name: versioned-other\n"
    "    match: '^OTHER '\n"
    "    strip:\n"
    "      - '^OTHER '\n"
)
_LOCAL_STRIPS_PREFIX_AND_DDMM = (
    "rules:\n"
    "  - name: local\n"
    "    match: '^LOCALPFX '\n"
    "    strip:\n"
    "      - '^LOCALPFX '\n"
    "      - '\\d{4}$'\n"
)


def test_frozen_etl_merchant_key_local_rule_wins_over_versioned(tmp_path, monkeypatch):
    # Both files match the description; the local rule comes first, so its strips are the ones
    # applied (the versioned rule alone would keep the trailing DDMM).
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(_VERSIONED_STRIPS_PREFIX)
    (tmp_path / "norm.local.yaml").write_text(_LOCAL_STRIPS_PREFIX_AND_DDMM)
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    row = _frozen_row(tmp_path, description="LOCALPFX PADARIA CENTRAL0101")
    assert row["etl_merchant_key"] == "PADARIA CENTRAL"
    assert row["description_normalized"] == "PADARIA CENTRAL"


def test_local_rules_come_first_and_versioned_rules_still_apply(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX,
                          local_yaml=_LOCAL_STRIPS_PREFIX_AND_DDMM)
    assert [r["name"] for r in ns["_norm_rules"]] == ["local", "versioned", "versioned-other"]
    nd = ns["_normalize_description"]
    assert nd("LOCALPFX MERCADO X0101") == "MERCADO X"
    assert nd("OTHER MERCADO X0101") == "MERCADO X0101", "versioned rule, local file present"


def test_missing_local_rules_file_is_not_an_error(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX)
    assert not (tmp_path / "norm.local.yaml").exists()
    assert [r["name"] for r in ns["_norm_rules"]] == ["versioned", "versioned-other"]
    assert ns["_normalize_description"]("LOCALPFX MERCADO X0101") == "MERCADO X0101"


def _local_rules_log_lines(out):
    return [line for line in out.splitlines() if "local rules file" in line]


def test_missing_local_rules_file_logs_the_file_to_create(tmp_path, monkeypatch, capsys):
    # Tests, CI and a new user run without the local file: the run goes on with no finding (a
    # finding would fail data_warnings, and the run, every day), and its log says what to create.
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(_VERSIONED_STRIPS_PREFIX)
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    row = _frozen_row(tmp_path, description="LOCALPFX PADARIA CENTRAL0101")
    assert row["etl_merchant_key"] == "PADARIA CENTRAL0101", "the run still writes"
    [line] = _local_rules_log_lines(capsys.readouterr().out)
    assert "norm.local.yaml" in line
    assert "merchant_normalization.local.example.yaml" in line
    assert not (tmp_path / "validation" / "merchant_normalization.json").exists()


def test_required_local_rules_file_missing_stops_the_run(tmp_path, monkeypatch):
    # Prod sets MERCHANT_NORM_LOCAL_REQUIRED=1: a missing local file stops the run before any
    # write, instead of writing merchant keys without the owner's bank rules.
    monkeypatch.setenv("MERCHANT_NORM_LOCAL_REQUIRED", "1")
    with pytest.raises(SystemExit) as exc:
        _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX)
    assert "norm.local.yaml" in str(exc.value)
    assert "merchant_normalization.local.example.yaml" in str(exc.value)


@pytest.mark.parametrize("local_yaml", ["", "rules:\n", "rules: []\n"])
def test_required_local_rules_file_without_rules_stops_the_run(tmp_path, monkeypatch, local_yaml):
    # An empty file would pass an existence check and the run would write without the rules.
    monkeypatch.setenv("MERCHANT_NORM_LOCAL_REQUIRED", "1")
    with pytest.raises(SystemExit) as exc:
        _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX, local_yaml=local_yaml)
    assert "norm.local.yaml" in str(exc.value)
    assert "has no rule" in str(exc.value)


def test_required_local_rules_file_present_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("MERCHANT_NORM_LOCAL_REQUIRED", "1")
    ns = _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX,
                          local_yaml=_LOCAL_STRIPS_PREFIX_AND_DDMM)
    assert ns["_normalize_description"]("LOCALPFX MERCADO X0101") == "MERCADO X"


def test_present_local_rules_file_logs_nothing(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(_VERSIONED_STRIPS_PREFIX)
    (tmp_path / "norm.local.yaml").write_text(_LOCAL_STRIPS_PREFIX_AND_DDMM)
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    _frozen_row(tmp_path, description="LOCALPFX PADARIA CENTRAL0101")
    assert _local_rules_log_lines(capsys.readouterr().out) == []


def test_local_rules_file_with_bare_rules_key_adds_no_rules(tmp_path, monkeypatch):
    # The template with its rule commented out leaves "rules:" with no value (None in YAML).
    ns = _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX, local_yaml="rules:\n")
    assert [r["name"] for r in ns["_norm_rules"]] == ["versioned", "versioned-other"]


def test_empty_local_rules_file_adds_no_rules(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch, _VERSIONED_STRIPS_PREFIX, local_yaml="")
    assert [r["name"] for r in ns["_norm_rules"]] == ["versioned", "versioned-other"]


# ── A10 institution-scoped normalization (df-s65) ─────────────────────────────────────────
# raw2cleansed still carries the full A10 machinery: _rule_applies_to + an institution-aware
# _normalize_description(description, institution=None). The live freeze derives with
# institution=None (match-all) because the shipped config's sole rule is un-scoped
# (df-s65 user-approved decision), so the scoping is NOT exercised by the pipeline — but the
# helpers accept `institution`, so we unit-test the scoping directly. NOTE: the frozen
# _merchant_key has NO institution parameter (3-arg, derives via _normalize_description with
# institution=None), so scoping is tested on _normalize_description / _rule_applies_to.

def _load_norm_funcs(tmp_path, monkeypatch, rules_yaml: str, local_yaml: str | None = None) -> dict:
    """Exec only raw2cleansed.py up to the argparse line so we get _normalize_description /
    _rule_applies_to / _merchant_key bound against a chosen rules config, without running the
    pipeline. The A10 institution machinery lives in raw2cleansed since df-s65. `local_yaml`,
    when given, is written as the local rules file beside the config (df-bsb2.3)."""
    scripts = _delta_util.SCRIPTS
    cfg = tmp_path / "norm.yaml"
    cfg.write_text(rules_yaml)
    if local_yaml is not None:
        (tmp_path / "norm.local.yaml").write_text(local_yaml)
    script = (scripts / "raw2cleansed" / "raw2cleansed.py").read_text()
    # keep everything before argument parsing (the pure helpers + rule load)
    head = script.split("parser = argparse.ArgumentParser()")[0]
    head = "\n".join(l for l in head.splitlines() if not l.strip().startswith("exec(open("))
    ns: dict = {"__file__": str(scripts / "raw2cleansed" / "raw2cleansed.py")}
    _delta_util.exec_functions(ns)
    # monkeypatch (not raw os.environ) so MERCHANT_NORM_CONFIG is restored after the test —
    # otherwise it leaks into later tests that load the live config (order-dependent failures).
    monkeypatch.setenv("MERCHANT_NORM_CONFIG", str(cfg))
    exec(head, ns)
    return ns


def test_a10_unscoped_rule_is_institution_invariant(tmp_path, monkeypatch):
    # The shipped example-debit-pos-prefix rule has NO institution: key. Its output must be identical for
    # institution=None, "Banco Fictício", and any other institution — proving A10 adds zero
    # output change for the current (un-scoped) config, consistent with the institution=None freeze.
    ns = _load_norm_funcs(tmp_path, monkeypatch,
        "rules:\n"
        "  - name: debit-pos-prefix\n"
        "    match: '^FICTPOS '\n"
        "    strip:\n"
        "      - '^FICTPOS '\n"
        "      - '\\d{4}$'\n"
    )
    nd = ns["_normalize_description"]
    desc = "FICTPOS MERCADO FICT0101"
    baseline = nd(desc, None)            # (description, institution)
    assert baseline == "MERCADO FICT"
    assert nd(desc, "Banco Fictício") == baseline
    assert nd(desc, "Nubank") == baseline
    assert nd(desc) == baseline          # institution default => None => match-all
    # And the frozen 3-arg _merchant_key (no institution param) derives the same value.
    assert ns["_merchant_key"](desc, None, None) == baseline


def test_a10_scoped_rule_applies_only_to_its_institution(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch,
        "rules:\n"
        "  - name: nubank-only\n"
        "    match: '^NU '\n"
        "    institution: Nubank\n"
        "    strip:\n"
        "      - '^NU '\n"
    )
    nd = ns["_normalize_description"]
    desc = "NU MERCADO X"
    # institution matches the rule's scope => rule applies (prefix stripped)
    assert nd(desc, "Nubank") == "MERCADO X"
    # institution does NOT match => rule skipped => only whitespace-collapse remains
    assert nd(desc, "Banco Fictício") == "NU MERCADO X"
    # institution=None => scoped rule is NOT eligible (None means "no institution known")
    assert nd(desc, None) == "NU MERCADO X"


def test_a10_scoped_rule_accepts_institution_list(tmp_path, monkeypatch):
    ns = _load_norm_funcs(tmp_path, monkeypatch,
        "rules:\n"
        "  - name: shared\n"
        "    match: '^PIX '\n"
        "    institution: [Banco Fictício, Nubank]\n"
        "    strip:\n"
        "      - '^PIX '\n"
    )
    nd = ns["_normalize_description"]
    desc = "PIX FULANO"
    assert nd(desc, "Banco Fictício") == "FULANO"
    assert nd(desc, "Nubank") == "FULANO"
    assert nd(desc, "Banco Modelo") == "PIX FULANO"  # not in list => skipped
