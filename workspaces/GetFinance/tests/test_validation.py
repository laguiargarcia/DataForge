"""Data validation and cross-layer reconciliation of the GetFinance pipeline (ADR 0004, df-ahua.3 and
df-ahua.22): validate() in functions.py, the rule lists next to it, and their wiring in the scripts.

Every frame is generated here; no value comes from real data. The rule lists are imported from
functions.py, so a new rule without a test in RULE_CASES fails test_every_rule_has_a_case.

The functions are loaded from _delta_util.SCRIPTS, this checkout's scripts.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests import _delta_util

SCRIPTS = _delta_util.SCRIPTS
RULE_LISTS = [
    "RAW_TRANSACTIONS_RULES", "RAW_ACCOUNTS_RULES", "RAW_BILLS_RULES", "RAW_INVESTMENTS_RULES",
    "CLEANSED_TRANSACTIONS_RULES", "FETCH_REMOVALS_RULES", "CARD_COPIES_RULES", "ROWS_KEPT_RULES", "IDS_KEPT_RULES",
    "ROWS_REMOVED_RULES", "ROWS_CARRIED_RULES",
    "TOTALS_CONSERVED_RULES", "DIM_ACCOUNT_RULES", "FACT_TRANSACTION_RULES",
    "FACT_CARD_STATEMENT_RULES", "FACT_FATURA_MES_RULES", "FACT_BALANCE_SNAPSHOT_RULES",
    "FACT_INVESTMENT_POSITION_RULES", "FACT_TRANSACTION_CLASSIFIED_RULES", "CONFERENCIA_SALDO_RULES",
    "CONFERENCIA_FATURA_RULES",
    "LANCAMENTOS_SUSPEITOS_RULES", "EXTRATO_RULES", "EXTRATO_SALDOS_RULES",
    "EXTRATO_ARQUIVOS_RULES", "VEREDITO_SUSPEITOS_RULES", "EXTRATO_SEM_LANCAMENTO_RULES",
    "ACERTO_POR_MOTIVO_RULES",
]


def _load_functions(root):
    path = SCRIPTS / "functions" / "functions.py"
    ns = {"__file__": str(path)}
    _delta_util.exec_functions(ns)
    ns["_connection_root"] = lambda layer: Path(root) / layer
    return ns


@pytest.fixture
def F(tmp_path):
    return _load_functions(tmp_path)


def _rule(F, list_name, rule_name):
    matches = [r for r in F[list_name] if r[0] == rule_name]
    assert len(matches) == 1, f"{list_name} has no single rule {rule_name!r}"
    return matches[0]


def _count(F, df, list_name, rule_name):
    return F["_offenders"](df, _rule(F, list_name, rule_name)[2], ["id"])[0]


# --- the function ------------------------------------------------------------------------------

SECRET_AMOUNT, SECRET_TEXT = "98765.43", "PADARIA SECRETA"


def _frame(**cols):
    return pl.DataFrame(cols)


# validate returns nothing, raises nothing and records an empty findings file on a clean frame.
def test_clean_frame_passes_and_records_no_finding(F, tmp_path):
    df = _frame(id=["a", "b"], v=[1, 2])
    rules = [("v is null", F["IMPOSSIBLE"], pl.col("v").is_null()),
             ("v is big", F["SUSPICIOUS"], pl.col("v") > 10)]
    assert F["validate"](df, "t.clean", rules) == []
    assert json.loads((tmp_path / "validation" / "t.clean.json").read_text()) == {
        "table": "t.clean", "findings": []}
    assert F["read_findings"]() == []


# A suspicious violation never raises: it is returned, printed and recorded for data_warnings.
def test_suspicious_violation_is_recorded_and_the_write_goes_on(F, capsys):
    df = _frame(id=["a", "b", "c"], v=[1, 20, 30])
    found = F["validate"](df, "t.sus", [("v is big", F["SUSPICIOUS"], pl.col("v") > 10)])
    assert found == [{"rule": "v is big", "severity": "suspicious", "count": 2, "keys": ["b", "c"]}]
    assert F["read_findings"]() == [("t.sus", found[0])]
    assert "t.sus: suspicious: v is big: 2 (e.g. b, c)" in capsys.readouterr().out


# An impossible violation raises one DataValidationError naming the table, rule, count and keys.
def test_impossible_violation_raises_with_rule_count_and_keys(F):
    df = _frame(id=["a", "b", "c"], v=[None, 2, None])
    rules = [("v is null", F["IMPOSSIBLE"], pl.col("v").is_null())]
    with pytest.raises(F["DataValidationError"]) as exc:
        F["validate"](df, "t.imp", rules)
    assert str(exc.value) == "t.imp: v is null: 2 (e.g. a, c)"


# Every impossible rule broken is in the one message, and the suspicious ones are still recorded.
def test_multiple_violations_are_reported_together(F):
    df = _frame(id=["a", "b"], v=[None, 50], w=[-1, 1])
    rules = [("v is null", F["IMPOSSIBLE"], pl.col("v").is_null()),
             ("w is negative", F["IMPOSSIBLE"], pl.col("w") < 0),
             ("v is big", F["SUSPICIOUS"], pl.col("v") > 10),
             ("never", F["IMPOSSIBLE"], pl.col("w") > 5)]
    with pytest.raises(F["DataValidationError"]) as exc:
        F["validate"](df, "t.many", rules)
    assert str(exc.value) == ("t.many: v is null: 1 (e.g. a); "
                              "w is negative: 1 (e.g. a)")
    assert [f["rule"] for _, f in F["read_findings"]()] == ["v is big"]


# Messages and findings quote key columns only: never an amount or a description.
def test_messages_quote_keys_and_no_other_value(F, tmp_path, capsys):
    df = _frame(id=["k1"], amount=[SECRET_AMOUNT], description=[SECRET_TEXT])
    rules = [("amount is set", F["IMPOSSIBLE"], pl.col("amount").is_not_null()),
             ("description is set", F["SUSPICIOUS"], pl.col("description").is_not_null())]
    with pytest.raises(F["DataValidationError"]) as exc:
        F["validate"](df, "t.secret", rules)
    recorded = (tmp_path / "validation" / "t.secret.json").read_text()
    for text in (str(exc.value), recorded, capsys.readouterr().out):
        assert "k1" in text
        assert SECRET_AMOUNT not in text
        assert SECRET_TEXT not in text


# At most SAMPLE_KEYS distinct keys are quoted; the count is every offending row.
def test_sample_is_capped_and_distinct(F):
    df = _frame(id=["a", "a"] + [f"k{i}" for i in range(10)])
    found = F["validate"](df, "t.cap", [("all", F["SUSPICIOUS"], pl.lit(True))])
    assert found[0]["count"] == 12
    assert found[0]["keys"] == ["a", "k0", "k1", "k2", "k3"]


# A composite key is quoted as "a|b" and a null key as "null".
def test_composite_and_null_keys_are_quoted(F):
    df = _frame(acc=["x", None], day=["2026-01-01", "2026-01-02"])
    found = F["validate"](df, "t.keys", [("all", F["SUSPICIOUS"], pl.lit(True))], ["acc", "day"])
    assert found[0]["keys"] == ["x|2026-01-01", "null|2026-01-02"]


# A column-set rule reports the missing columns as its keys.
def test_column_set_rule_names_the_missing_columns(F):
    df = _frame(id=["x"], a=[1])
    rules = [("cols", F["IMPOSSIBLE"], ["a", "b", "c"])]
    with pytest.raises(F["DataValidationError"], match=r"cols: 2 \(e.g. b, c\)"):
        F["validate"](df, "t.cols", rules)


# A column a row rule reads but the frame lacks reads as null: a not-null rule fires on every row
# (a field Pluggy stopped sending), an enum rule on an optional column stays quiet.
def test_missing_column_reads_as_null(F):
    df = _frame(id=["a", "b"])
    rules = [("x is null", F["SUSPICIOUS"], pl.col("x").is_null()),
             ("y unknown", F["SUSPICIOUS"], F["not_in"]("y", ["A"]))]
    found = F["validate"](df, "t.absent", rules)
    assert [(f["rule"], f["count"]) for f in found] == [("x is null", 2)]


# A rule that cannot be computed on the null stand-in of a missing column counts every row.
def test_rule_on_a_missing_column_that_cannot_be_computed_fires(F):
    df = _frame(id=["a", "b"])
    found = F["validate"](df, "t.arith", [("neg", F["SUSPICIOUS"], -pl.col("amount") > 0)])
    assert found == [{"rule": "neg", "severity": "suspicious", "count": 2,
                      "keys": ["missing column amount"]}]


# A broken rule on columns the frame has is a bug: it raises instead of reporting a violation.
def test_a_broken_rule_on_present_columns_raises(F):
    df = _frame(id=["a"], s=["x"])
    rules = [("bad", F["SUSPICIOUS"], -pl.col("s") > 0)]   # built lazily: it only fails when evaluated
    with pytest.raises(pl.exceptions.PolarsError):
        F["validate"](df, "t.bug", rules)


def test_unknown_severity_is_refused(F):
    df = _frame(id=["a"])
    rules = [("r", "warning", pl.lit(True))]
    with pytest.raises(ValueError, match="unknown severity"):
        F["validate"](df, "t.sev", rules)


# A fixed problem clears: the next run rewrites the check's file without the finding.
def test_a_clean_run_clears_the_previous_findings(F):
    rule = [("v is big", F["SUSPICIOUS"], pl.col("v") > 10)]
    F["validate"](_frame(id=["a"], v=[50]), "t.clear", rule)
    assert len(F["read_findings"]()) == 1
    F["validate"](_frame(id=["a"], v=[5]), "t.clear", rule)
    assert F["read_findings"]() == []


# Property: the count is the number of rows where the rule holds, and the keys quoted are the first
# distinct keys of those rows, in input order.
@settings(max_examples=100, deadline=None, database=None)
@given(rows=st.lists(st.tuples(st.sampled_from(["k0", "k1", "k2", "k3", "k4", "k5", "k6", None]),
                               st.booleans()), max_size=30))
def test_count_and_keys_match_the_offending_rows(rows):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        F = _load_functions(tmp)
        df = pl.DataFrame(rows, schema={"id": pl.Utf8, "bad": pl.Boolean}, orient="row")
        found = F["validate"](df, "t.prop", [("bad", F["SUSPICIOUS"], pl.col("bad"))])
    offending = [k for k, bad in rows if bad]
    if not offending:
        assert found == []
        return
    distinct = list(dict.fromkeys("null" if k is None else k for k in offending))
    assert found[0]["count"] == len(offending)
    assert found[0]["keys"] == distinct[:5]


# layer_totals: one row per group, 0 on the side where a group is absent.
def test_layer_totals_counts_both_sides(F):
    before = _frame(g=["a", "a", "b"], v=[1.0, 2.0, 5.0])
    after = _frame(g=["a", "c"], v=[3.0, 4.0])
    out = F["layer_totals"](before, after, ["g"], "v").sort("g").rows(named=True)
    assert out == [
        {"g": "a", "rows_before": 2, "total_before": 3.0, "rows_after": 1, "total_after": 3.0},
        {"g": "b", "rows_before": 1, "total_before": 5.0, "rows_after": 0, "total_after": 0.0},
        {"g": "c", "rows_before": 0, "total_before": 0.0, "rows_after": 1, "total_after": 4.0},
    ]


# --- one case per rule: it fires on a minimal bad frame and stays quiet on the good one ----------

def _utf8(**cols):
    return pl.DataFrame(cols, schema={c: pl.Utf8 for c in cols})


# Raw frames carry the day of their partition as ingestion_date, as read_raw adds it.
RAW_TXN = dict(id=["t1"], date=["2026-01-15T00:00:00.000Z"], amount=["-10.5"], category=["c"],
               categoryId=["01000000"], description=["pão"], descriptionRaw=["d"], merchant=[None],
               accountName=["a"], accountId=["a1"], ingested_at=["2026-01-15"], type=["DEBIT"],
               status=["POSTED"], currencyCode=["BRL"], amountInAccountCurrency=[None],
               ingestion_date=["2026-01-15"])
RAW_ACC = dict(id=["a1"], itemId=["i1"], type=["BANK"], name=["n"], balance=["10.0"],
               currencyCode=["BRL"], ingested_at=["2026-01-15"], ingestion_date=["2026-01-15"])
RAW_BILL = dict(id=["b1"], accountId=["a2"], dueDate=["2026-01-20T00:00:00.000Z"],
                totalAmount=["100.0"], totalAmountCurrencyCode=["BRL"], minimumPaymentAmount=["10.0"],
                payments=["[]"], allowsInstallments=["True"], ingested_at=["2026-01-15"],
                ingestion_date=["2026-01-15"])
RAW_INV = dict(id=["h1"], itemId=["i1"], name=["CDB fictício"], type=["FIXED_INCOME"], balance=["100.0"],
               amountOriginal=["90.0"], amountProfit=[None], currencyCode=["BRL"],
               ingested_at=["2026-01-15"], ingestion_date=["2026-01-15"])
MOJIBAKE = "p\u00c3\u00a3o"   # "pão" read back as cp1252
FUTURE, PAST = "2999-01-01", "1999-12-31"
CLEANSED_TXN = dict(id=["t1"], amount=[-10.0], account_type=["BANK"], txn_type=["DEBIT"],
                    ingested_at=["2026-01-15"], cleansed_ingested_at=["2026-01-14"])
TOTALS = dict(id=["g"], rows_before=[1], rows_after=[1], total_before=[10.0], total_after=[10.0])
GONE = dict(accountId=["a1"], ids_in_window=[100], ids_newly_removed=[21])
COPIES = dict(statement_id=["f1"], candidatos=[2], removidos=[1], retida=[False])
DIM_ACC = dict(id=["a1", "a2"], pluggy_account_id=["a1", "a2"],
               account_uuid=["bank-checking-0001", "bank-credit-0002"],
               institution=["Banco Fictício", "Banco Fictício"])
FACT_TXN = {c: [None, None] for c in [
    "etl_merchant_key", "description_normalized", "category", "categoryId",
    "counterparty_document", "payment_method", "cc_bill_id", "cc_installment_number",
    "cc_total_installments", "cc_purchase_date", "status", "txn_type", "operation_type",
    "cash_flag", "etl_tag", "netting_applied", "income_source_key"]}
FACT_TXN.update(id=["t1", "t2"], date=["2026-01-15", "2026-01-16"], account_uuid=["u1", "u2"],
                account_type=["BANK", "CREDIT"], amount=[-10.0, 20.0], amount_signed=[-10.0, -20.0])
CARD = {c: [None, None] for c in [
    "minimum_payment", "paid_date", "payment_type", "paid_in_full", "finance_charges",
    "allows_installments", "currency", "updated_at"]}
CARD.update(id=["s1", "s2"], statement_id=["s1", "s2"], account_uuid=["u2", "u2"],
            anomes_key=[202601, 202602], due_date=["2026-01-20", "2026-02-20"],
            statement_amount=[300.0, 310.0], paid_amount=[300.0, None])
FATURA = dict(id=["x", "y"], account_uuid=["u2", "u2"], anomes_key=[202601, 202602],
              mes_inicio=["2026-01-01", "2026-02-01"], due_date=["2026-01-20", None],
              statement_id=["s1", None], valor_fatura=[300.0, 50.0], valor_pago=[300.0, 0.0],
              valor_pendente=[0.0, 50.0], is_open=[False, True])
SNAP = {c: [None, None] for c in [
    "available", "card_outstanding", "available_credit_limit", "credit_limit", "minimum_payment",
    "statement_close_date", "statement_due_date", "currency"]}
SNAP.update(id=["x", "y"], account_uuid=["u1", "u1"], snapshot_date=["2026-01-01", "2026-01-02"],
            account_type=["BANK", "BANK"], balance=[10.0, 12.0])
POS = dict(id=["x", "y"], holding_uuid=["h1", "h1"], snapshot_date=["2026-01-01", "2026-01-02"],
           invested_amount=[100.0, 100.0], current_value=[101.0, 102.0], currency=["BRL", "BRL"])
POS["return"] = [1.0, 2.0]
CLASSIFIED = dict(id=["t1", "t2"])
CONF = dict(account_uuid=["u1", "u1"], de=[date(2026, 1, 1), date(2026, 1, 2)],
            ate=[date(2026, 1, 2), date(2026, 1, 3)], coletado_em=[date(2026, 1, 2), date(2026, 1, 3)],
            saldo_de=[10.0, 12.0], saldo_ate=[12.0, 12.0],
            variacao_saldo=[2.0, 0.0], soma_lancamentos=[2.0, 0.0], n_lancamentos=[1, 0],
            diferenca=[0.0, 0.0], diferenca_acumulada=[0.0, 0.0], estado=["fecha", "em_aberto"])
CONF_FATURA = dict(account_uuid=["u2", "u2"], statement_id=["s1", "s2"],
                   due_date=[date(2026, 1, 20), date(2026, 2, 20)], total_fatura=[300.0, 310.0],
                   saldo_anterior=[0.0, 0.0], soma_lancamentos=[300.0, 320.0], n_lancamentos=[3, 4],
                   diferenca_lancamentos=[0.0, -10.0], estado_lancamentos=["fecha", "nao_fecha"],
                   pago=[300.0, 0.0], n_pagamentos=[1, 0], diferenca_pagamento=[0.0, 310.0],
                   estado_pagamento=["fecha", "a_vencer"])
SUSP = dict(transaction_id=["t1", "t1"], account_uuid=["u1", "u1"], date=[date(2026, 1, 2)] * 2,
            amount=[-5.0, -5.0], description=["COMPRA", "COMPRA"], status=["PENDING", "PENDING"],
            motivo=["sumiu_da_coleta", "pendente_que_sumiu"], primeira_coleta=[date(2026, 1, 2)] * 2,
            ultima_coleta=[date(2026, 1, 3)] * 2, n_coletas=[2, 2], intervalo_de=[date(2026, 1, 1), None],
            intervalo_ate=[date(2026, 1, 2), None], intervalo_estado=["fecha", None])
EXTRATO = dict(arquivo=["a.pdf", "a.pdf"], account_uuid=["u1", "u1"], dia=[date(2026, 1, 2)] * 2, ordem=[1, 2],
               descricao=["COMPRA", "COMPRA"], valor=[-5.0, -5.0], saldo_do_dia=[90.0, 90.0])
EXTRATO_SALDOS = dict(account_uuid=["u1", "u1"], dia=[date(2026, 1, 1), date(2026, 1, 2)], arquivo=["a.pdf"] * 2,
                      saldo_do_dia=[100.0, 90.0], saldo_anterior=[None, 100.0], soma_lancamentos=[0.0, -10.0],
                      n_lancamentos=[0, 2], diferenca=[None, 0.0], fecha=[None, True])
EXTRATO_ARQUIVOS = dict(arquivo=["a.pdf", "b.pdf"], account_uuid=["u1", "u1"],
                        emitido_em=[date(2026, 1, 3), date(2026, 1, 4)], n_lancamentos=[2, 0], n_saldos=[2, 1],
                        linhas_ignoradas=[0, 1])
VEREDITO = dict(transaction_id=["t1", "t2"], account_uuid=["u1", "u1"], date=[date(2026, 1, 2)] * 2,
                amount=[-5.0, -6.0], motivo=["sumiu_da_coleta"] * 2, veredito=["no_extrato", "fora_do_extrato"],
                dia_banco=[date(2026, 1, 2)] * 2, extrato_dia=[date(2026, 1, 3), None], extrato_ordem=[1, None])
SEM_LANCAMENTO = dict(arquivo=["a.pdf", "a.pdf"], account_uuid=["u1", "u1"], dia=[date(2026, 1, 2)] * 2,
                      ordem=[1, 2], descricao=["COMPRA", "COMPRA"], valor=[-5.0, -5.0])
ACERTO = dict(motivo=["sumiu_da_coleta", "aplicacao_automatica"], n=[3, 0], n_no_extrato=[1, 0],
              n_fora_do_extrato=[2, 0])


def _twice(base, **second):
    # Two rows: the base row, then a copy with `second` applied (a 1-row base).
    return {c: v + [second.get(c, v[0])] for c, v in base.items()}


def _set(base, **changes):
    out = {c: list(v) for c, v in base.items()}
    out.update({c: v for c, v in changes.items() if v is not ...})
    for c, v in changes.items():
        if v is ...:
            del out[c]
    return out


# (rule list, rule name, frame builder, good columns, bad columns). Raw frames are all text, as raw is.
RULE_CASES = [
    ("RAW_TRANSACTIONS_RULES", "columns the script reads are present", _utf8, RAW_TXN,
     _set(RAW_TXN, descriptionRaw=...)),
    ("RAW_TRANSACTIONS_RULES", "id is null", _utf8, RAW_TXN, _set(RAW_TXN, id=[None])),
    ("RAW_TRANSACTIONS_RULES", "ingested_at is null", _utf8, RAW_TXN,
     _set(RAW_TXN, ingested_at=[None])),
    ("RAW_TRANSACTIONS_RULES", "ingested_at is not a date", _utf8, RAW_TXN,
     _set(RAW_TXN, ingested_at=["n/a"])),
    ("RAW_TRANSACTIONS_RULES", "accountId is null", _utf8, RAW_TXN, _set(RAW_TXN, accountId=[None])),
    ("RAW_TRANSACTIONS_RULES", "date is missing or not a date", _utf8, RAW_TXN,
     _set(RAW_TXN, date=["15/01/2026"])),
    ("RAW_TRANSACTIONS_RULES", "amount is missing or not a number", _utf8, RAW_TXN,
     _set(RAW_TXN, amount=["R$ 10"])),
    ("RAW_TRANSACTIONS_RULES", "type is not DEBIT or CREDIT", _utf8, RAW_TXN,
     _set(RAW_TXN, type=["TRANSFER"])),
    ("RAW_TRANSACTIONS_RULES", "status is not POSTED or PENDING", _utf8, RAW_TXN,
     _set(RAW_TXN, status=["CANCELLED"])),
    ("RAW_TRANSACTIONS_RULES", "amountInAccountCurrency is not a number", _utf8, RAW_TXN,
     _set(RAW_TXN, currencyCode=["USD"], amountInAccountCurrency=["R$ 55"])),
    ("RAW_TRANSACTIONS_RULES", "currencyCode is not BRL and amountInAccountCurrency is null", _utf8, RAW_TXN,
     _set(RAW_TXN, currencyCode=["USD"])),
    ("RAW_ACCOUNTS_RULES", "columns the script reads are present", _utf8, RAW_ACC,
     _set(RAW_ACC, currencyCode=...)),
    ("RAW_ACCOUNTS_RULES", "id is null", _utf8, RAW_ACC, _set(RAW_ACC, id=[None])),
    ("RAW_ACCOUNTS_RULES", "ingested_at is null", _utf8, RAW_ACC, _set(RAW_ACC, ingested_at=[None])),
    ("RAW_ACCOUNTS_RULES", "ingested_at is not a date", _utf8, RAW_ACC, _set(RAW_ACC, ingested_at=["ontem"])),
    ("RAW_ACCOUNTS_RULES", "balance is not a number", _utf8, RAW_ACC, _set(RAW_ACC, balance=["n/a"])),
    ("RAW_ACCOUNTS_RULES", "balance is null", _utf8, RAW_ACC, _set(RAW_ACC, balance=[None])),
    ("RAW_ACCOUNTS_RULES", "itemId is null", _utf8, RAW_ACC, _set(RAW_ACC, itemId=[None])),
    ("RAW_ACCOUNTS_RULES", "type is not BANK or CREDIT", _utf8, RAW_ACC,
     _set(RAW_ACC, type=["INVESTMENT"])),
    ("RAW_BILLS_RULES", "id is null", _utf8, RAW_BILL, _set(RAW_BILL, id=...)),
    ("RAW_BILLS_RULES", "ingested_at is null", _utf8, RAW_BILL, _set(RAW_BILL, ingested_at=[None])),
    ("RAW_BILLS_RULES", "ingested_at is not a date", _utf8, RAW_BILL,
     _set(RAW_BILL, ingested_at=["15/01/2026"])),
    ("RAW_BILLS_RULES", "accountId is null", _utf8, RAW_BILL, _set(RAW_BILL, accountId=[None])),
    ("RAW_BILLS_RULES", "dueDate is not a date", _utf8, RAW_BILL, _set(RAW_BILL, dueDate=["soon"])),
    ("RAW_BILLS_RULES", "totalAmount is not a number", _utf8, RAW_BILL,
     _set(RAW_BILL, totalAmount=["1,5"])),
    ("RAW_BILLS_RULES", "minimumPaymentAmount is not a number", _utf8, RAW_BILL,
     _set(RAW_BILL, minimumPaymentAmount=["x"])),
    ("RAW_BILLS_RULES", "dueDate is null", _utf8, RAW_BILL, _set(RAW_BILL, dueDate=[None])),
    ("RAW_BILLS_RULES", "totalAmount is null", _utf8, RAW_BILL, _set(RAW_BILL, totalAmount=[None])),
    ("RAW_BILLS_RULES", "payments is null", _utf8, RAW_BILL, _set(RAW_BILL, payments=...)),
    ("RAW_BILLS_RULES", "allowsInstallments is not True or False", _utf8, RAW_BILL,
     _set(RAW_BILL, allowsInstallments=["yes"])),
    ("RAW_INVESTMENTS_RULES", "id is null", _utf8, RAW_INV, _set(RAW_INV, id=[None])),
    ("RAW_INVESTMENTS_RULES", "ingested_at is null", _utf8, RAW_INV, _set(RAW_INV, ingested_at=...)),
    ("RAW_INVESTMENTS_RULES", "ingested_at is not a date", _utf8, RAW_INV,
     _set(RAW_INV, ingested_at=["2026-13-01"])),
    ("RAW_INVESTMENTS_RULES", "balance is not a number", _utf8, RAW_INV, _set(RAW_INV, balance=["-"])),
    ("RAW_INVESTMENTS_RULES", "amountOriginal is not a number", _utf8, RAW_INV,
     _set(RAW_INV, amountOriginal=["?"])),
    ("RAW_INVESTMENTS_RULES", "amountProfit is not a number", _utf8, RAW_INV,
     _set(RAW_INV, amountProfit=["lucro"])),
    ("RAW_INVESTMENTS_RULES", "balance is null", _utf8, RAW_INV, _set(RAW_INV, balance=[None])),
    ("RAW_INVESTMENTS_RULES", "type is not a Pluggy investment type", _utf8, RAW_INV,
     _set(RAW_INV, type=["CRYPTO"])),
    ("CLEANSED_TRANSACTIONS_RULES", "accountId matches no raw account", _frame, CLEANSED_TXN,
     _set(CLEANSED_TXN, account_type=[None])),
    ("CLEANSED_TRANSACTIONS_RULES", "amount sign contradicts account and transaction type",
     _frame, CLEANSED_TXN, _set(CLEANSED_TXN, amount=[10.0])),
    ("CLEANSED_TRANSACTIONS_RULES", "ingested_at is older than the row already in cleansed",
     _frame, CLEANSED_TXN, _set(CLEANSED_TXN, cleansed_ingested_at=["2026-01-16"])),
    ("ROWS_KEPT_RULES", "rows lost", _frame, TOTALS, _set(TOTALS, rows_after=[0])),
    ("IDS_KEPT_RULES", "rows lost", _frame, TOTALS, _set(TOTALS, rows_after=[0])),
    ("IDS_KEPT_RULES", "id written more than once", _frame, TOTALS,
     _set(TOTALS, rows_before=[1], rows_after=[2])),
    ("FETCH_REMOVALS_RULES", "too many ids to remove from the latest fetch: none removed from the account",
     _frame, _set(GONE, ids_newly_removed=[20]), GONE),
    ("CARD_COPIES_RULES", "card entries that look copied and the bill does not tell which: none removed",
     _frame, COPIES, _set(COPIES, removidos=[0], retida=[True])),
    ("ROWS_REMOVED_RULES", "removed id still written", _frame, _set(TOTALS, rows_after=[0]), TOTALS),
    ("ROWS_CARRIED_RULES", "rows not carried one to one", _frame, TOTALS,
     _set(TOTALS, rows_after=[2])),
    ("TOTALS_CONSERVED_RULES", "rows not carried one to one", _frame, TOTALS,
     _set(TOTALS, rows_before=[0])),
    ("TOTALS_CONSERVED_RULES", "amount total not conserved", _frame, TOTALS,
     _set(TOTALS, total_after=[10.01])),
    ("DIM_ACCOUNT_RULES", "pluggy_account_id is null", _frame, DIM_ACC,
     _set(DIM_ACC, pluggy_account_id=["a1", None])),
    ("DIM_ACCOUNT_RULES", "pluggy_account_id is not unique", _frame, DIM_ACC,
     _set(DIM_ACC, pluggy_account_id=["a1", "a1"])),
    ("DIM_ACCOUNT_RULES", "account_uuid is not unique", _frame, DIM_ACC,
     _set(DIM_ACC, account_uuid=["bank-checking-0001", "bank-checking-0001"])),
    ("DIM_ACCOUNT_RULES", "account_uuid is null", _frame, DIM_ACC,
     _set(DIM_ACC, account_uuid=["bank-checking-0001", None])),
    ("DIM_ACCOUNT_RULES", "account_uuid has no account digits", _frame, DIM_ACC,
     _set(DIM_ACC, account_uuid=["bank-checking-", "bank-credit-0002"])),
    ("FACT_TRANSACTION_RULES", "expected columns are present", _frame, FACT_TXN,
     _set(FACT_TXN, cash_flag=...)),
    ("FACT_TRANSACTION_RULES", "id is null", _frame, FACT_TXN, _set(FACT_TXN, id=["t1", None])),
    ("FACT_TRANSACTION_RULES", "id is not unique", _frame, FACT_TXN,
     _set(FACT_TXN, id=["t1", "t1"])),
    ("FACT_TRANSACTION_RULES", "account_uuid is null", _frame, FACT_TXN,
     _set(FACT_TXN, account_uuid=[None, "u2"])),
    ("FACT_TRANSACTION_RULES", "date is null", _frame, FACT_TXN,
     _set(FACT_TXN, date=["2026-01-15", None])),
    ("FACT_TRANSACTION_RULES", "amount is null", _frame, FACT_TXN,
     _set(FACT_TXN, amount=[None, 20.0], amount_signed=[None, -20.0])),
    ("FACT_TRANSACTION_RULES", "amount_signed is not amount (BANK) or -amount (CREDIT)",
     _frame, FACT_TXN, _set(FACT_TXN, amount_signed=[-10.0, 20.0])),
    ("FACT_CARD_STATEMENT_RULES", "statement_id is null", _frame, CARD,
     _set(CARD, statement_id=["s1", None])),
    ("FACT_CARD_STATEMENT_RULES", "statement_id is not unique", _frame, CARD,
     _set(CARD, statement_id=["s1", "s1"])),
    ("FACT_CARD_STATEMENT_RULES", "more than one statement per account and due month", _frame,
     CARD, _set(CARD, anomes_key=[202601, 202601])),
    ("FACT_CARD_STATEMENT_RULES", "account_uuid is null", _frame, CARD,
     _set(CARD, account_uuid=["u2", None])),
    ("FACT_CARD_STATEMENT_RULES", "due_date is null", _frame, CARD,
     _set(CARD, due_date=["2026-01-20", None])),
    ("FACT_FATURA_MES_RULES", "more than one row per account and due month", _frame, FATURA,
     _set(FATURA, anomes_key=[202601, 202601])),
    ("FACT_FATURA_MES_RULES", "account_uuid is null", _frame, FATURA,
     _set(FATURA, account_uuid=[None, "u2"])),
    ("FACT_FATURA_MES_RULES", "anomes_key is null", _frame, FATURA,
     _set(FATURA, anomes_key=[202601, None])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "more than one row per account and day", _frame, SNAP,
     _set(SNAP, snapshot_date=["2026-01-01", "2026-01-01"])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "account_uuid is null", _frame, SNAP,
     _set(SNAP, account_uuid=["u1", None])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "snapshot_date is null", _frame, SNAP,
     _set(SNAP, snapshot_date=[None, "2026-01-02"])),
    ("FACT_INVESTMENT_POSITION_RULES", "more than one row per holding and day", _frame, POS,
     _set(POS, snapshot_date=["2026-01-01", "2026-01-01"])),
    ("FACT_INVESTMENT_POSITION_RULES", "holding_uuid is null", _frame, POS,
     _set(POS, holding_uuid=["h1", None])),
    ("FACT_INVESTMENT_POSITION_RULES", "snapshot_date is null", _frame, POS,
     _set(POS, snapshot_date=["2026-01-01", None])),
    ("FACT_TRANSACTION_CLASSIFIED_RULES", "id is null", _frame, CLASSIFIED,
     _set(CLASSIFIED, id=["t1", None])),
    ("FACT_TRANSACTION_CLASSIFIED_RULES", "id is not unique", _frame, CLASSIFIED,
     _set(CLASSIFIED, id=["t1", "t1"])),
    # The rules every raw entity shares (df-ahua.4.1, df-ahua.4.3).
    *[case for list_name, base in (("RAW_TRANSACTIONS_RULES", RAW_TXN), ("RAW_ACCOUNTS_RULES", RAW_ACC),
                                   ("RAW_BILLS_RULES", RAW_BILL), ("RAW_INVESTMENTS_RULES", RAW_INV))
      for case in [
          (list_name, "ingested_at is not its partition's day", _utf8, base,
           _set(base, ingested_at=["2026-01-16"])),
          (list_name, "ingested_at is after the run day", _utf8, base,
           _set(base, ingested_at=[FUTURE], ingestion_date=[FUTURE])),
          (list_name, "id appears twice in one fetch with different content", _utf8, base,
           _twice(base, ingestion_date="2026-01-15", **{c: "x" for c in base if c not in (
               "id", "ingested_at", "ingestion_date")})),
      ]],
    ("RAW_TRANSACTIONS_RULES", "date is more than a year after ingested_at or before 2000", _utf8, RAW_TXN,
     _set(RAW_TXN, date=["2027-01-17T00:00:00.000Z"])),
    ("RAW_TRANSACTIONS_RULES", "type is null", _utf8, RAW_TXN, _set(RAW_TXN, type=[None])),
    ("RAW_TRANSACTIONS_RULES", "status is null", _utf8, RAW_TXN, _set(RAW_TXN, status=...)),
    ("RAW_TRANSACTIONS_RULES", "description is null on every row of a fetch", _utf8, RAW_TXN,
     _set(RAW_TXN, description=[None])),
    ("RAW_TRANSACTIONS_RULES", "category is null on every row of a fetch", _utf8, RAW_TXN,
     _set(RAW_TXN, category=[None])),
    ("RAW_TRANSACTIONS_RULES", "description has broken encoding", _utf8, RAW_TXN,
     _set(RAW_TXN, description=[MOJIBAKE])),
    ("RAW_TRANSACTIONS_RULES", "category has broken encoding", _utf8, RAW_TXN,
     _set(RAW_TXN, category=["Alimenta\u00c3\u00a7\u00c3\u00a3o"])),
    ("RAW_ACCOUNTS_RULES", "currencyCode is not BRL", _utf8, RAW_ACC, _set(RAW_ACC, currencyCode=["USD"])),
    ("RAW_BILLS_RULES", "columns the script reads are present", _utf8, RAW_BILL,
     _set(RAW_BILL, totalAmountCurrencyCode=...)),
    ("RAW_BILLS_RULES", "dueDate is more than a year after ingested_at or before 2000", _utf8, RAW_BILL,
     _set(RAW_BILL, dueDate=[PAST + "T00:00:00.000Z"])),
    ("RAW_BILLS_RULES", "minimumPaymentAmount is null on every row of a fetch", _utf8, RAW_BILL,
     _set(RAW_BILL, minimumPaymentAmount=[None])),
    ("RAW_BILLS_RULES", "allowsInstallments is null on every row of a fetch", _utf8, RAW_BILL,
     _set(RAW_BILL, allowsInstallments=...)),
    ("RAW_BILLS_RULES", "totalAmountCurrencyCode is not BRL", _utf8, RAW_BILL,
     _set(RAW_BILL, totalAmountCurrencyCode=[None])),
    ("RAW_INVESTMENTS_RULES", "columns the script reads are present", _utf8, RAW_INV,
     _set(RAW_INV, name=...)),
    ("RAW_INVESTMENTS_RULES", "name is null on every row of a fetch", _utf8, RAW_INV,
     _set(RAW_INV, name=[None])),
    ("RAW_INVESTMENTS_RULES", "type is null on every row of a fetch", _utf8, RAW_INV,
     _set(RAW_INV, type=[None])),
    ("RAW_INVESTMENTS_RULES", "amountOriginal is null on every row of a fetch", _utf8, RAW_INV,
     _set(RAW_INV, amountOriginal=[None])),
    ("RAW_INVESTMENTS_RULES", "name has broken encoding", _utf8, RAW_INV,
     _set(RAW_INV, name=["CDB fict\u00c3\u00adcio"])),
    ("RAW_INVESTMENTS_RULES", "currencyCode is not BRL", _utf8, RAW_INV, _set(RAW_INV, currencyCode=["EUR"])),
    ("DIM_ACCOUNT_RULES", "institution is a placeholder", _frame, DIM_ACC,
     _set(DIM_ACC, institution=["Banco Fictício", "unknown-999"])),
    ("DIM_ACCOUNT_RULES", "institution has broken encoding", _frame, DIM_ACC,
     _set(DIM_ACC, institution=["Banco Fict\u00c3\u00adcio", "Banco Fictício"])),
    ("FACT_CARD_STATEMENT_RULES", "expected columns are present", _frame, CARD, _set(CARD, updated_at=...)),
    ("FACT_CARD_STATEMENT_RULES", "due_date is not a date", _frame, CARD,
     _set(CARD, due_date=["2026-01-20", "n/a"])),
    ("FACT_CARD_STATEMENT_RULES", "due_date is more than a year after the run day or before 2000", _frame,
     CARD, _set(CARD, due_date=["2026-01-20", FUTURE])),
    ("FACT_CARD_STATEMENT_RULES", "statement_amount is not a number", _frame, CARD,
     _set(CARD, statement_amount=["300.0", "310,0"])),
    ("FACT_CARD_STATEMENT_RULES", "paid_amount is not a number", _frame, CARD,
     _set(CARD, paid_amount=["300.0", "n/a"])),
    ("FACT_CARD_STATEMENT_RULES", "statement_amount is null", _frame, CARD,
     _set(CARD, statement_amount=[300.0, None])),
    ("FACT_FATURA_MES_RULES", "expected columns are present", _frame, FATURA, _set(FATURA, is_open=...)),
    ("FACT_FATURA_MES_RULES", "due_date is null on a closed bill", _frame, FATURA,
     _set(FATURA, due_date=[None, None])),
    ("FACT_FATURA_MES_RULES", "due_date is not a date", _frame, FATURA, _set(FATURA, due_date=["x", None])),
    ("FACT_FATURA_MES_RULES", "due_date is more than a year after the run day or before 2000", _frame,
     FATURA, _set(FATURA, due_date=[PAST, None])),
    ("FACT_FATURA_MES_RULES", "valor_fatura is not a number", _frame, FATURA,
     _set(FATURA, valor_fatura=["300.0", "R$ 50"])),
    ("FACT_FATURA_MES_RULES", "valor_fatura is null", _frame, FATURA, _set(FATURA, valor_fatura=[300.0, None])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "expected columns are present", _frame, SNAP, _set(SNAP, currency=...)),
    ("FACT_BALANCE_SNAPSHOT_RULES", "snapshot_date is not a date", _frame, SNAP,
     _set(SNAP, snapshot_date=["2026-01-01", "ontem"])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "snapshot_date is after the run day", _frame, SNAP,
     _set(SNAP, snapshot_date=["2026-01-01", FUTURE])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "balance is not a number", _frame, SNAP,
     _set(SNAP, balance=["10.0", "1,2"])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "balance is null", _frame, SNAP, _set(SNAP, balance=[10.0, None])),
    ("FACT_BALANCE_SNAPSHOT_RULES", "account_type is null", _frame, SNAP,
     _set(SNAP, account_type=["BANK", None])),
    ("FACT_INVESTMENT_POSITION_RULES", "expected columns are present", _frame, POS, _set(POS, currency=...)),
    ("FACT_INVESTMENT_POSITION_RULES", "snapshot_date is not a date", _frame, POS,
     _set(POS, snapshot_date=["2026-01-01", "-"])),
    ("FACT_INVESTMENT_POSITION_RULES", "snapshot_date is after the run day", _frame, POS,
     _set(POS, snapshot_date=["2026-01-01", FUTURE])),
    ("FACT_INVESTMENT_POSITION_RULES", "current_value is not a number", _frame, POS,
     _set(POS, current_value=["101.0", "n/a"])),
    ("FACT_INVESTMENT_POSITION_RULES", "current_value is null", _frame, POS,
     _set(POS, current_value=[101.0, None])),
    ("FACT_INVESTMENT_POSITION_RULES", "current_value is negative", _frame, POS,
     _set(POS, current_value=[101.0, -102.0])),
    ("CONFERENCIA_SALDO_RULES", "expected columns are present", _frame, CONF, _set(CONF, estado=...)),
    ("CONFERENCIA_SALDO_RULES", "account_uuid is null", _frame, CONF, _set(CONF, account_uuid=["u1", None])),
    ("CONFERENCIA_SALDO_RULES", "more than one row per account and interval end", _frame, CONF,
     _set(CONF, ate=[date(2026, 1, 3), date(2026, 1, 3)])),
    ("CONFERENCIA_SALDO_RULES", "de is not before ate", _frame, CONF,
     _set(CONF, de=[date(2026, 1, 2), date(2026, 1, 2)])),
    ("CONFERENCIA_SALDO_RULES", "estado is not a known state", _frame, CONF,
     _set(CONF, estado=["fecha", "fechou"])),
    ("CONFERENCIA_FATURA_RULES", "expected columns are present", _frame, CONF_FATURA,
     _set(CONF_FATURA, estado_pagamento=...)),
    ("CONFERENCIA_FATURA_RULES", "account_uuid is null", _frame, CONF_FATURA,
     _set(CONF_FATURA, account_uuid=["u2", None])),
    ("CONFERENCIA_FATURA_RULES", "more than one row per statement", _frame, CONF_FATURA,
     _set(CONF_FATURA, statement_id=["s1", "s1"])),
    ("CONFERENCIA_FATURA_RULES", "estado_lancamentos is not a known state", _frame, CONF_FATURA,
     _set(CONF_FATURA, estado_lancamentos=["fecha", "a_vencer"])),
    ("CONFERENCIA_FATURA_RULES", "estado_pagamento is not a known state", _frame, CONF_FATURA,
     _set(CONF_FATURA, estado_pagamento=["fecha", None])),
    ("LANCAMENTOS_SUSPEITOS_RULES", "expected columns are present", _frame, SUSP, _set(SUSP, motivo=...)),
    ("LANCAMENTOS_SUSPEITOS_RULES", "transaction_id is null", _frame, SUSP,
     _set(SUSP, transaction_id=["t1", None])),
    ("LANCAMENTOS_SUSPEITOS_RULES", "account_uuid is null", _frame, SUSP, _set(SUSP, account_uuid=["u1", None])),
    ("LANCAMENTOS_SUSPEITOS_RULES", "more than one row per transaction and reason", _frame, SUSP,
     _set(SUSP, motivo=["sumiu_da_coleta", "sumiu_da_coleta"])),
    ("EXTRATO_RULES", "expected columns are present", _frame, EXTRATO, _set(EXTRATO, saldo_do_dia=...)),
    ("EXTRATO_RULES", "account_uuid is null", _frame, EXTRATO, _set(EXTRATO, account_uuid=["u1", None])),
    ("EXTRATO_RULES", "dia is null", _frame, EXTRATO, _set(EXTRATO, dia=[date(2026, 1, 2), None])),
    ("EXTRATO_RULES", "valor is null", _frame, EXTRATO, _set(EXTRATO, valor=[-5.0, None])),
    ("EXTRATO_RULES", "more than one row per account, day and position", _frame, EXTRATO,
     _set(EXTRATO, ordem=[1, 1])),
    ("EXTRATO_SALDOS_RULES", "expected columns are present", _frame, EXTRATO_SALDOS,
     _set(EXTRATO_SALDOS, fecha=...)),
    ("EXTRATO_SALDOS_RULES", "account_uuid is null", _frame, EXTRATO_SALDOS,
     _set(EXTRATO_SALDOS, account_uuid=["u1", None])),
    ("EXTRATO_SALDOS_RULES", "saldo_do_dia is null", _frame, EXTRATO_SALDOS,
     _set(EXTRATO_SALDOS, saldo_do_dia=[100.0, None])),
    ("EXTRATO_SALDOS_RULES", "more than one balance per account and day", _frame, EXTRATO_SALDOS,
     _set(EXTRATO_SALDOS, dia=[date(2026, 1, 2)] * 2)),
    ("EXTRATO_ARQUIVOS_RULES", "expected columns are present", _frame, EXTRATO_ARQUIVOS,
     _set(EXTRATO_ARQUIVOS, linhas_ignoradas=...)),
    ("EXTRATO_ARQUIVOS_RULES", "account_uuid is null", _frame, EXTRATO_ARQUIVOS,
     _set(EXTRATO_ARQUIVOS, account_uuid=["u1", None])),
    ("EXTRATO_ARQUIVOS_RULES", "more than one row per file", _frame, EXTRATO_ARQUIVOS,
     _set(EXTRATO_ARQUIVOS, arquivo=["a.pdf", "a.pdf"])),
    ("VEREDITO_SUSPEITOS_RULES", "expected columns are present", _frame, VEREDITO,
     _set(VEREDITO, extrato_ordem=...)),
    ("VEREDITO_SUSPEITOS_RULES", "transaction_id is null", _frame, VEREDITO,
     _set(VEREDITO, transaction_id=["t1", None])),
    ("VEREDITO_SUSPEITOS_RULES", "account_uuid is null", _frame, VEREDITO, _set(VEREDITO, account_uuid=["u1", None])),
    ("VEREDITO_SUSPEITOS_RULES", "more than one row per transaction and reason", _frame, VEREDITO,
     _set(VEREDITO, transaction_id=["t1", "t1"])),
    ("VEREDITO_SUSPEITOS_RULES", "veredito is not a known verdict", _frame, VEREDITO,
     _set(VEREDITO, veredito=["no_extrato", "talvez"])),
    ("VEREDITO_SUSPEITOS_RULES", "the statement line is set exactly when the verdict is no_extrato", _frame,
     VEREDITO, _set(VEREDITO, extrato_dia=[None, None])),
    ("EXTRATO_SEM_LANCAMENTO_RULES", "expected columns are present", _frame, SEM_LANCAMENTO,
     _set(SEM_LANCAMENTO, descricao=...)),
    ("EXTRATO_SEM_LANCAMENTO_RULES", "account_uuid is null", _frame, SEM_LANCAMENTO,
     _set(SEM_LANCAMENTO, account_uuid=["u1", None])),
    ("EXTRATO_SEM_LANCAMENTO_RULES", "more than one row per account, day and position", _frame, SEM_LANCAMENTO,
     _set(SEM_LANCAMENTO, ordem=[1, 1])),
    ("ACERTO_POR_MOTIVO_RULES", "expected columns are present", _frame, ACERTO, _set(ACERTO, n=...)),
    ("ACERTO_POR_MOTIVO_RULES", "motivo is null", _frame, ACERTO, _set(ACERTO, motivo=["sumiu_da_coleta", None])),
    ("ACERTO_POR_MOTIVO_RULES", "more than one row per reason", _frame, ACERTO,
     _set(ACERTO, motivo=["sumiu_da_coleta"] * 2)),
    ("ACERTO_POR_MOTIVO_RULES", "the verdicts add up to more than n", _frame, ACERTO, _set(ACERTO, n=[2, 0])),
]


@pytest.mark.parametrize("case", RULE_CASES, ids=[f"{c[0]}:{c[1]}" for c in RULE_CASES])
def test_rule_fires_on_bad_frame_and_is_quiet_on_good(F, case):
    list_name, rule_name, build, good, bad = case
    assert _count(F, build(**good), list_name, rule_name) == 0
    assert _count(F, build(**bad), list_name, rule_name) > 0


# Every rule of every list has a case above (and every case names a real rule).
def test_every_rule_has_a_case(F):
    listed = {(name, rule[0]) for name in RULE_LISTS for rule in F[name]}
    cased = {(c[0], c[1]) for c in RULE_CASES}
    assert listed == cased
    # Every list exported by functions.py is in RULE_LISTS.
    assert {k for k in F if k.endswith("_RULES")} == set(RULE_LISTS)


# The good frames pass their whole list, so each case's bad frame breaks only what it targets.
@pytest.mark.parametrize("list_name,build,good", [
    ("RAW_TRANSACTIONS_RULES", _utf8, RAW_TXN), ("RAW_ACCOUNTS_RULES", _utf8, RAW_ACC),
    ("RAW_BILLS_RULES", _utf8, RAW_BILL), ("RAW_INVESTMENTS_RULES", _utf8, RAW_INV),
    ("CLEANSED_TRANSACTIONS_RULES", _frame, CLEANSED_TXN),
    ("TOTALS_CONSERVED_RULES", _frame, TOTALS), ("IDS_KEPT_RULES", _frame, TOTALS),
    ("DIM_ACCOUNT_RULES", _frame, DIM_ACC), ("FACT_TRANSACTION_RULES", _frame, FACT_TXN),
    ("FACT_CARD_STATEMENT_RULES", _frame, CARD), ("FACT_FATURA_MES_RULES", _frame, FATURA),
    ("FACT_BALANCE_SNAPSHOT_RULES", _frame, SNAP),
    ("FACT_INVESTMENT_POSITION_RULES", _frame, POS),
    ("FACT_TRANSACTION_CLASSIFIED_RULES", _frame, CLASSIFIED),
    ("CONFERENCIA_SALDO_RULES", _frame, CONF),
    ("CONFERENCIA_FATURA_RULES", _frame, CONF_FATURA),
    ("LANCAMENTOS_SUSPEITOS_RULES", _frame, SUSP),
    ("EXTRATO_RULES", _frame, EXTRATO),
    ("EXTRATO_SALDOS_RULES", _frame, EXTRATO_SALDOS),
    ("EXTRATO_ARQUIVOS_RULES", _frame, EXTRATO_ARQUIVOS),
    ("VEREDITO_SUSPEITOS_RULES", _frame, VEREDITO),
    ("EXTRATO_SEM_LANCAMENTO_RULES", _frame, SEM_LANCAMENTO),
    ("ACERTO_POR_MOTIVO_RULES", _frame, ACERTO),
])
def test_good_frame_passes_its_whole_list(F, list_name, build, good):
    assert F["validate"](build(**good), f"t.{list_name}", F[list_name]) == []


# The sign rule over the four (account type, transaction type) pairs of Pluggy's convention.
@pytest.mark.parametrize("account_type,txn_type,amount,fires", [
    ("BANK", "CREDIT", 5.0, False), ("BANK", "CREDIT", -5.0, True),
    ("BANK", "DEBIT", -5.0, False), ("BANK", "DEBIT", 5.0, True),
    ("CREDIT", "DEBIT", 5.0, False), ("CREDIT", "DEBIT", -5.0, True),
    ("CREDIT", "CREDIT", -5.0, False), ("CREDIT", "CREDIT", 5.0, True),
    ("BANK", "DEBIT", 0.0, False), ("LOAN", "DEBIT", 5.0, False), ("BANK", None, 5.0, False),
])
def test_sign_rule_follows_pluggy_convention(F, account_type, txn_type, amount, fires):
    df = pl.DataFrame({"id": ["t"], "account_type": [account_type], "txn_type": [txn_type],
                       "amount": [amount]})
    count = _count(F, df, "CLEANSED_TRANSACTIONS_RULES",
                   "amount sign contradicts account and transaction type")
    assert (count > 0) == fires


# A foreign currency is unsafe only without the converted value cleansed takes (df-ahua.3.1). A null or
# absent currencyCode (old partitions) reads as the account's.
@pytest.mark.parametrize("currency,converted,fires", [
    ("BRL", None, False), ("USD", "55.0", False), ("USD", None, True), ("EUR", None, True),
    (None, None, False), (..., None, False), ("USD", ..., True)])
def test_foreign_currency_rule_fires_only_without_the_converted_amount(F, currency, converted, fires):
    df = _utf8(**_set(RAW_TXN, currencyCode=currency if currency is ... else [currency],
                      amountInAccountCurrency=converted if converted is ... else [converted]))
    rule = "currencyCode is not BRL and amountInAccountCurrency is null"
    assert _rule(F, "RAW_TRANSACTIONS_RULES", rule)[1] == F["SUSPICIOUS"]
    assert (_count(F, df, "RAW_TRANSACTIONS_RULES", rule) > 0) == fires


# The run-day rules take `today` as a parameter: with it injected, the bounds are exact. A day of
# slack after the run day is allowed (two clocks may disagree); text and typed dates read alike.
@pytest.mark.parametrize("value,fires", [
    ("2026-01-16", False), ("2026-01-16T23:59:59Z", False), ("2026-01-17", True), (None, False),
    (date(2026, 1, 16), False), (date(2026, 1, 17), True)])
def test_after_run_day_bound(F, value, fires):
    df = pl.DataFrame({"id": ["x"], "d": [value]})
    count = F["_offenders"](df, F["after_run_day"]("d", today=date(2026, 1, 15)), ["id"])[0]
    assert (count > 0) == fires


# implausible_day: before 2000, or more than 366 days after the reference column (or the run day).
@pytest.mark.parametrize("value,ref,fires", [
    ("2027-01-16", "2026-01-15", False), ("2027-01-17", "2026-01-15", True),
    ("2000-01-01", "2026-01-15", False), ("1999-12-31", "2026-01-15", True),
    ("2025-01-01T00:00:00.000Z", "2026-01-15", False), (None, "2026-01-15", False),
    ("2027-01-17", None, False)])
def test_implausible_day_bounds(F, value, ref, fires):
    df = _utf8(id=["x"], d=[value], r=[ref])
    count = F["_offenders"](df, F["implausible_day"]("d", "r"), ["id"])[0]
    assert (count > 0) == fires


def test_implausible_day_against_the_run_day(F):
    df = _utf8(id=["x", "y"], d=["2027-01-16", "2027-01-17"])
    found = F["_offenders"](df, F["implausible_day"]("d", today=date(2026, 1, 15)), ["id"])
    assert found == (1, ["y"])


# The same id twice in one fetch: an identical copy is harmless (latest_by_key keeps one of two equal
# rows); a copy that differs leaves an arbitrary winner until the next fetch wins, so it is a warning
# (raw is never rewritten: an impossible rule would block every run after it). The same id in two
# fetches is a refresh.
@pytest.mark.parametrize("second,fires", [
    ({}, False), ({"amount": "-11.5"}, True), ({"ingested_at": "2026-01-16", "ingestion_date": "2026-01-16"},
                                               False)])
def test_id_twice_in_a_fetch(F, second, fires):
    df = _utf8(**_twice(RAW_TXN, **second))
    rule = "id appears twice in one fetch with different content"
    assert _rule(F, "RAW_TRANSACTIONS_RULES", rule)[1] == F["SUSPICIOUS"]
    assert (_count(F, df, "RAW_TRANSACTIONS_RULES", rule) > 0) == fires


# A column null on every row of one fetch fires on that fetch only, not on a fetch that has it.
def test_null_in_a_fetch_fires_on_that_fetch_only(F):
    df = _utf8(id=["a", "b", "c"], ingested_at=["2026-01-14", "2026-01-15", "2026-01-15"],
               description=["x", None, None])
    rule = _rule(F, "RAW_TRANSACTIONS_RULES", "description is null on every row of a fetch")
    assert F["_offenders"](df, rule[2], ["id"]) == (2, ["b", "c"])
    one = df.with_columns(description=pl.Series(["x", "y", None]))
    assert F["_offenders"](one, rule[2], ["id"])[0] == 0


# Property: Portuguese text never reads as broken encoding, and the same text decoded as cp1252 always
# does when it has a non-ASCII letter (the bug class of df-ahua.4.4).
@settings(max_examples=200, deadline=None, database=None)
@given(text=st.text(alphabet="abcçdeéêfghiíjlmnoóôõpqrstuúvxzãâàAÁÂÃÀÇÉÊÍÓÔÕÚ 0123456789-/.", min_size=1,
                    max_size=30))
def test_mojibake_rule_tells_broken_text_from_portuguese(text):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        F = _load_functions(tmp)
        broken = text.encode("utf-8").decode("cp1252", errors="replace")
        df = pl.DataFrame({"id": ["ok", "broken"], "t": [text, broken]})
        _, keys = F["_offenders"](df, F["mojibake_rule"]("t")[2], ["id"])
    assert keys == ([] if text.isascii() else ["broken"])


# read_raw gives each partition's rows the day of its path, which the partition-day rule compares.
def test_read_raw_adds_the_partition_day(F, tmp_path):
    _raw_partition(tmp_path, "transactions", _set(RAW_TXN, ingestion_date=...), "2026-01-15")
    _raw_partition(tmp_path, "transactions", _set(RAW_TXN, id=["t2"], ingested_at=["2026-01-15"],
                                                  ingestion_date=...), "2026-01-16")
    files = sorted(str(p) for p in (tmp_path / "raw" / "transactions").glob("*/data.parquet"))
    raw = F["read_raw"](files)
    assert raw.select("id", "ingestion_date").rows() == [("t1", "2026-01-15"), ("t2", "2026-01-16")]
    assert _count(F, raw, "RAW_TRANSACTIONS_RULES", "ingested_at is not its partition's day") == 1


# --- wiring in the scripts -----------------------------------------------------------------------

VALIDATED_SCRIPTS = {
    "raw2cleansed/raw2cleansed.py": ["RAW_TRANSACTIONS_RULES", "CLEANSED_TRANSACTIONS_RULES",
                                     "FETCH_REMOVALS_RULES", "CARD_COPIES_RULES", "IDS_KEPT_RULES",
                                     "ROWS_KEPT_RULES", "ROWS_REMOVED_RULES"],
    "raw2cleansed/raw2cleansed_accounts.py": ["RAW_ACCOUNTS_RULES", "IDS_KEPT_RULES"],
    "raw2cleansed/raw2cleansed_bills.py": ["RAW_BILLS_RULES", "IDS_KEPT_RULES"],
    "raw2cleansed/raw2cleansed_investments.py": ["RAW_INVESTMENTS_RULES", "IDS_KEPT_RULES"],
    "cleansed2curated/cleansed2curated_accounts.py": ["DIM_ACCOUNT_RULES", "ROWS_CARRIED_RULES"],
    "cleansed2curated/cleansed2curated.py": ["FACT_TRANSACTION_RULES", "TOTALS_CONSERVED_RULES"],
    "cleansed2curated/fact_card_statement.py": ["FACT_CARD_STATEMENT_RULES", "ROWS_CARRIED_RULES"],
    "cleansed2curated/fact_fatura_mes.py": ["FACT_FATURA_MES_RULES", "ROWS_CARRIED_RULES"],
    "cleansed2curated/fact_balance_snapshot.py": ["FACT_BALANCE_SNAPSHOT_RULES", "ROWS_CARRIED_RULES"],
    "cleansed2curated/fact_investment_position.py": ["FACT_INVESTMENT_POSITION_RULES",
                                                     "ROWS_CARRIED_RULES"],
    "report/classify.py": ["FACT_TRANSACTION_CLASSIFIED_RULES", "ROWS_CARRIED_RULES"],
    "cleansed2curated/cleansed2curated_conferencia_saldo.py": ["CONFERENCIA_SALDO_RULES"],
    "cleansed2curated/cleansed2curated_conferencia_fatura.py": ["CONFERENCIA_FATURA_RULES",
                                                                "ROWS_CARRIED_RULES"],
    "cleansed2curated/cleansed2curated_lancamentos_suspeitos.py": ["LANCAMENTOS_SUSPEITOS_RULES"],
    "cleansed2curated/cleansed2curated_extrato.py": ["EXTRATO_RULES", "EXTRATO_SALDOS_RULES",
                                                       "EXTRATO_ARQUIVOS_RULES"],
}


# Each script passes its rule lists to validate, and every check has its own name: the name is the
# findings file, so two checks sharing one would overwrite each other's findings.
def test_scripts_validate_with_their_rule_lists_and_unique_check_names():
    names = []
    for script, lists in VALIDATED_SCRIPTS.items():
        source = (SCRIPTS / script).read_text(encoding="utf-8")
        for rule_list in lists:
            assert rule_list in source, f"{script} does not use {rule_list}"
        names += re.findall(r'"((?:raw|cleansed|curated)\.[a-z_]+(?: vs [a-z_ ]+)?)"', source)
    assert len(names) == len(set(names)), names
    assert len(names) == sum(len(v) for v in VALIDATED_SCRIPTS.values())


# A validate call comes before the write it protects in every script.
@pytest.mark.parametrize("script", sorted(VALIDATED_SCRIPTS))
def test_validation_precedes_the_write(script):
    source = (SCRIPTS / script).read_text(encoding="utf-8")
    first_check = source.index("validate(")
    # The last write is the one of the data (accounts and bills write an empty table earlier, when
    # raw has no partition at all).
    writes = list(re.finditer(r"(write_delta_overwrite|upsert_delta_table)\((args\.target|\"cleansed\")",
                              source))
    assert writes
    assert first_check < writes[-1].start()


def _raw_partition(root, entity, rows, day):
    part = root / "raw" / entity / f"ingestion_date={day}"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema={c: pl.Utf8 for c in rows}).write_parquet(part / "data.parquet")


def _raw_txn(**changes):
    return _set(RAW_TXN, **changes)


def _run_transactions(tmp_path):
    return _delta_util.run_script("raw2cleansed", "raw2cleansed",
                                  ["--source", "raw", "--target", "cleansed"], tmp_path)


def _seed_raw_accounts(tmp_path):
    _raw_partition(tmp_path, "accounts", _set(RAW_ACC, id=["a1"]), "2026-01-15")


# df-ahua.15.1 on the validated path: latest_by_key still lets a NULL ingested_at win
# (test_latest_by_key pins that), but raw2cleansed refuses such raw and writes nothing.
def test_transactions_script_refuses_a_null_ingested_at(tmp_path):
    _seed_raw_accounts(tmp_path)
    _raw_partition(tmp_path, "transactions", _raw_txn(ingested_at=["2026-01-01"]), "2026-01-01")
    _raw_partition(tmp_path, "transactions", _raw_txn(ingested_at=[None], amount=["-99.0"]),
                   "2026-01-02")
    with pytest.raises(Exception, match="raw.transactions: ingested_at is null") as exc:
        _run_transactions(tmp_path)
    assert type(exc.value).__name__ == "DataValidationError"
    assert not (tmp_path / "cleansed" / "transactions").exists()


# df-ahua.15.2 on the validated path: a NULL-id raw row is refused on every run, so it is never
# inserted (the MERGE would have inserted it again each run), and cleansed stays as it was.
def test_transactions_script_refuses_a_null_id_on_every_run(tmp_path):
    _seed_raw_accounts(tmp_path)
    _raw_partition(tmp_path, "transactions", _raw_txn(), "2026-01-15")
    _run_transactions(tmp_path)
    before = _delta_util.read(tmp_path, "cleansed", "transactions")
    _raw_partition(tmp_path, "transactions", _set(RAW_TXN, id=[None], ingested_at=["2026-01-16"]),
                   "2026-01-16")
    for _ in range(2):
        with pytest.raises(Exception, match="raw.transactions: id is null"):
            _run_transactions(tmp_path)
    assert _delta_util.read(tmp_path, "cleansed", "transactions").equals(before)


# Good raw goes through every check of the transactions script and is written.
def test_transactions_script_writes_good_raw(tmp_path):
    _seed_raw_accounts(tmp_path)
    _raw_partition(tmp_path, "transactions", _raw_txn(), "2026-01-15")
    _run_transactions(tmp_path)
    _run_transactions(tmp_path)
    assert _delta_util.read(tmp_path, "cleansed", "transactions")["id"].to_list() == ["t1"]


# A sign that contradicts Pluggy's convention stops the transactions script before the merge.
def test_transactions_script_refuses_a_flipped_sign(tmp_path):
    _seed_raw_accounts(tmp_path)
    _raw_partition(tmp_path, "transactions", _raw_txn(amount=["10.5"]), "2026-01-15")
    with pytest.raises(Exception, match="cleansed.transactions: amount sign contradicts"):
        _run_transactions(tmp_path)
    assert not (tmp_path / "cleansed" / "transactions").exists()


# --- data_warnings -------------------------------------------------------------------------------

def _run_data_warnings(tmp_path):
    script = SCRIPTS / "validation" / "data_warnings.py"
    code = _delta_util.compile_script(script)
    ns = _load_functions(tmp_path)
    ns["__file__"] = str(script)
    old_argv, sys.argv = sys.argv, ["data_warnings"]
    try:
        exec(code, ns)
    except SystemExit as e:
        return e.code
    finally:
        sys.argv = old_argv
    return 0


# Without findings data_warnings exits 0, so the alert_warnings message task is skipped.
def test_data_warnings_passes_without_findings(F, tmp_path, capsys):
    F["validate"](_frame(id=["a"]), "t.ok", [("never", F["SUSPICIOUS"], pl.lit(False))])
    assert _run_data_warnings(tmp_path) == 0
    assert "no suspicious findings" in capsys.readouterr().out


# With findings it prints each one (ids only) and exits 1, which triggers alert_warnings; the
# summary is the last line, so the message's {failed_tasks_summary} always carries it.
def test_data_warnings_fails_and_lists_findings(F, tmp_path, capsys):
    F["validate"](_frame(id=["k1"], amount=[SECRET_AMOUNT]), "raw.x",
                  [("amount is set", F["SUSPICIOUS"], pl.col("amount").is_not_null())])
    F["validate"](_frame(id=["k2"]), "curated.y", [("always", F["SUSPICIOUS"], pl.lit(True))])
    assert _run_data_warnings(tmp_path) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "2 suspicious finding(s) in curated.y, raw.x"
    assert "curated.y: always: 1 (e.g. k2)" in out
    assert SECRET_AMOUNT not in "\n".join(out)
