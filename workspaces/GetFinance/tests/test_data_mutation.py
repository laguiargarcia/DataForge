"""Data mutation of the GetFinance data checks (ADR 0004, df-ahua.4).

Mutation testing asks whether the tests would notice wrong code; this asks whether the data checks
(validate and the rule lists of functions.py, df-ahua.3 and df-ahua.22) would notice wrong DATA.
For every boundary that has a rule list, a builder makes a small valid dataset (made-up values only),
which passes every check of that boundary with no finding. Then each corruption of data_mutator that
applies to one of its frames is run through the REAL validate and rule lists, called the way the
script calls them, and the test asserts the outcome recorded in OUTCOMES:

- "impossible: <check>: <rule>" / "suspicious: <check>: <rule>": that rule fired (the exact set);
- TOLERATED: no rule fires, by design (the reason is in OUTCOMES; where the claim is that the output
  is still right, the test checks the output too);
- a HOLE: no rule fires and corrupt data would reach curated. The case is a strict xfail naming its
  bead, so it flips when the rule lands.

The raw2cleansed transactions path is also run end to end, as subprocesses over a temp workspace, in
test_data_mutation_e2e.py.

The functions are loaded from _delta_util.SCRIPTS, this checkout's scripts.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from tests import _delta_util
from tests import data_mutator as dm
from tests.data_mutator import Shape


def load_functions(root):
    path = _delta_util.SCRIPTS / "functions" / "functions.py"
    ns = {"__file__": str(path)}
    _delta_util.exec_functions(ns)
    ns["_connection_root"] = lambda layer: Path(root) / layer
    return ns


@pytest.fixture(scope="module")
def _functions(tmp_path_factory):
    return load_functions(tmp_path_factory.mktemp("functions"))


@pytest.fixture
def F(_functions, tmp_path):
    # One load per module (exec of functions.py is the slow part); each test writes its findings
    # under its own tmp_path.
    _functions["_connection_root"] = lambda layer: tmp_path / layer
    return _functions


def _text(rows):
    # Raw is all text (landing2raw writes every Pluggy field as VARCHAR).
    cols = list(rows[0])
    return pl.DataFrame({c: [r[c] for r in rows] for c in cols}, schema={c: pl.Utf8 for c in cols})


def _raw(rows):
    # The combined raw as read_raw returns it: each row with the day of its partition's path, which
    # is its ingested_at in a good dataset (A16).
    return _text(rows).with_columns(ingestion_date=pl.col("ingested_at"))


def _days(start, n):
    return [start + timedelta(days=k) for k in range(n)]


# --- good datasets (made-up values only) ----------------------------------------------------------
# Two raw partitions (ingested_at is the day of the fetch, A16); t1 is fetched again on day two.
# Signs follow Pluggy's convention: acc0 is a BANK account, acc1 a CREDIT card.
D1, D2 = "2026-01-10", "2026-01-11"


def raw_transaction(spec, **changes):
    # spec = (id, day of January, amount, type, account, ingested_at)
    id_, day, amount, type_, account, ingested_at = spec
    row = dict(id=id_, date=f"2026-01-0{day}T00:00:00.000Z", amount=amount, category="Alimentação",
               categoryId="01000000", description="pão de queijo", descriptionRaw="pão de queijo",
               merchant=None, accountName="Conta Corrente" if account == "acc0" else "Cartão",
               accountId=account, ingested_at=ingested_at, type=type_, status="POSTED",
               currencyCode="BRL", amountInAccountCurrency=None)
    row.update(changes)
    if "description" in changes:
        row["descriptionRaw"] = changes["description"]
    return row


RAW_TRANSACTION_ROWS = [
    raw_transaction(("t0", 5, "-12.5", "DEBIT", "acc0", D1)),
    raw_transaction(("t1", 6, "2500.0", "CREDIT", "acc0", D1), description="salário"),
    raw_transaction(("t2", 7, "35.9", "DEBIT", "acc1", D1), status="PENDING", description="café"),
    raw_transaction(("t1", 6, "2500.0", "CREDIT", "acc0", D2), description="salário"),
    raw_transaction(("t3", 8, "-35.9", "CREDIT", "acc1", D2), description="estorno café"),
    # A card purchase in another currency: `amount` is the foreign value, amountInAccountCurrency the
    # BRL one the bank converted, which cleansed takes (df-ahua.3.1). t5: test_data_mutation_e2e
    # lands its new row as t4.
    raw_transaction(("t5", 9, "10.0", "DEBIT", "acc1", D2), description="loja fictícia",
                    currencyCode="USD", amountInAccountCurrency="52.5"),
]


def raw_account(id_, type_, ingested_at, balance):
    return dict(id=id_, itemId="item0", type=type_, name="Conta Corrente" if type_ == "BANK" else "Cartão",
                balance=balance, currencyCode="BRL", ingested_at=ingested_at)


RAW_ACCOUNT_ROWS = [raw_account("acc0", "BANK", D1, "100.0"), raw_account("acc1", "CREDIT", D1, "-50.0"),
                    raw_account("acc0", "BANK", D2, "120.0"), raw_account("acc1", "CREDIT", D2, "-60.0")]


def raw_bill(id_, month, ingested_at):
    return dict(id=id_, accountId="acc1", dueDate=f"2026-{month:02d}-20T00:00:00.000Z",
                totalAmount="300.0", totalAmountCurrencyCode="BRL", minimumPaymentAmount="30.0",
                financeCharges="[]", payments="[]", allowsInstallments="True",
                updatedAt=f"{ingested_at}T00:00:00.000Z", ingested_at=ingested_at)


def raw_investment(id_, ingested_at, balance):
    return dict(id=id_, itemId="item0", type="FIXED_INCOME", name="CDB fictício", balance=balance,
                amountOriginal="1000.0", amountProfit=None, currencyCode="BRL", ingested_at=ingested_at)


def cleansed_transactions_inputs():
    # The frames raw2cleansed.py builds before its checks: this run's rows (latest raw per id, typed),
    # the latest raw account per id, and cleansed.transactions as the last run left it.
    new = pl.DataFrame({
        "id": ["t0", "t1", "t2", "t3"], "accountId": ["acc0", "acc0", "acc1", "acc1"],
        "date": _days(date(2026, 1, 5), 4), "amount": [-12.5, 2500.0, 35.9, -35.9],
        "txn_type": ["DEBIT", "CREDIT", "DEBIT", "CREDIT"],
        "ingested_at": [D1, D2, D1, D2]})
    accounts = pl.DataFrame({"accountId": ["acc0", "acc1"], "account_type": ["BANK", "CREDIT"]})
    previous = (new.filter(pl.col("id").is_in(["t0", "t1"]))
                .join(accounts, on="accountId").with_columns(ingested_at=pl.lit(D1))
                .vstack(pl.DataFrame({"id": ["t9"], "accountId": ["acc1"], "date": [date(2025, 12, 30)],
                                      "amount": [10.0], "txn_type": ["DEBIT"], "ingested_at": ["2025-12-31"],
                                      "account_type": ["CREDIT"]}).select(
                    "id", "accountId", "date", "amount", "txn_type", "ingested_at", "account_type")))
    # latest: txn_latest, the raw after latest_by_key. `new` is new_rows, built from it: a corruption
    # of `new` is a row going wrong between latest_by_key and the MERGE.
    return {"new": new, "latest": new, "accounts": accounts, "previous": previous}


def _new_rows(frames):
    # raw2cleansed.py's left join of the latest raw account (account_type) onto this run's rows.
    return frames["new"].join(frames["accounts"], on="accountId", how="left")


def cleansed_transactions_derive(frames):
    # written: what the MERGE leaves (this run's rows replace theirs, absent ids persist).
    new = _new_rows(frames)
    previous = frames["previous"].select(new.columns)
    written = pl.concat([previous.filter(~pl.col("id").is_in(new["id"].implode())), new])
    return {"written": written}


def cleansed_transactions_checks(F, frames):
    previous, written = frames["previous"], frames["written"]
    return [
        ("cleansed.transactions",
         lambda: _new_rows(frames).join(
             previous.select("id", pl.col("ingested_at").alias("cleansed_ingested_at")), on="id", how="left"),
         "CLEANSED_TRANSACTIONS_RULES", "id"),
        ("cleansed.transactions vs raw", lambda: F["layer_totals"](frames["latest"], written, ["id"]),
         "IDS_KEPT_RULES", "id"),
        ("cleansed.transactions vs last run", lambda: F["layer_totals"](previous, written, ["id"]),
         "ROWS_KEPT_RULES", "id"),
    ]


DIM_ACCOUNT = pl.DataFrame({
    "account_uuid": ["banco-ficticio-checking-0001", "banco-ficticio-credit-0002"],
    "pluggy_account_id": ["acc0", "acc1"], "name": ["Banco Fictício Conta Corrente", "Banco Fictício Cartão"],
    "institution": ["Banco Fictício", "Banco Fictício"], "type": ["BANK", "CREDIT"], "currency": ["BRL", "BRL"]})

FACT_ROWS = {  # id -> (accountId, txn_type, amount)
    "t0": ("acc0", "DEBIT", -12.5), "t1": ("acc0", "CREDIT", 2500.0),
    "t2": ("acc1", "DEBIT", 35.9), "t3": ("acc1", "CREDIT", -35.9)}
UNSET_FACT_COLUMNS = ["counterparty_document", "payment_method", "cc_bill_id", "cc_installment_number",
                      "cc_total_installments", "cc_purchase_date", "operation_type", "etl_tag",
                      "income_source_key"]


def fact_transaction_inputs():
    # cleansed.transactions, dim_account and dim_calendar as cleansed2curated.py reads them, and the
    # columns the script derives from each transaction other than amount and account_uuid (fact_all).
    ids = list(FACT_ROWS)
    accounts = [FACT_ROWS[i][0] for i in ids]
    transactions = pl.DataFrame({"id": ids, "accountId": accounts, "amount": [FACT_ROWS[i][2] for i in ids]})
    fact = {c: pl.Series(c, [None] * 4, dtype=pl.Utf8) for c in UNSET_FACT_COLUMNS}
    fact.update(
        id=ids, date=_days(date(2026, 1, 5), 4),
        account_type=["BANK" if a == "acc0" else "CREDIT" for a in accounts],
        etl_merchant_key=["pão de queijo", "salário", "café", "café"],
        description_normalized=["pão de queijo", "salário", "café", "estorno café"],
        category=["Alimentação"] * 4, categoryId=["01000000"] * 4, is_transfer=[False] * 4,
        status=["POSTED", "POSTED", "PENDING", "POSTED"],
        txn_type=[FACT_ROWS[i][1] for i in ids], cash_flag=[True, True, False, False],
        netting_applied=[False, False, True, True])
    calendar = pl.DataFrame({"date": _days(date(2025, 1, 1), 1095)})
    return {"transactions": transactions, "dim_account": DIM_ACCOUNT, "calendar": calendar,
            "fact_all": pl.DataFrame(fact)}


def fact_transaction_derive(frames, columns):
    # cleansed2curated.py: the inner join gives each transaction its account's account_uuid (and drops
    # a transaction whose account has no dim_account row); amount comes from cleansed and
    # amount_signed is -amount on a CREDIT card.
    dim = frames["dim_account"].select("pluggy_account_id", "account_uuid")
    fact = (frames["fact_all"].join(frames["transactions"], on="id")
            .join(dim, left_on="accountId", right_on="pluggy_account_id", how="inner")
            .with_columns(amount_signed=pl.when(pl.col("account_type") == "CREDIT").then(-pl.col("amount"))
                          .otherwise(pl.col("amount"))))
    return {"fact": fact.select(columns)}


CALENDAR_RULE = "date has no row in dim_calendar"  # added to FACT_TRANSACTION_RULES by cleansed2curated.py


def fact_transaction_checks(F, frames):
    transactions, dim = frames["transactions"], frames["dim_account"]
    calendar_rule = (CALENDAR_RULE, F["SUSPICIOUS"],
                     F["not_in"]("date", frames["calendar"]["date"]))
    def totals():
        return F["layer_totals"](
            transactions,
            frames["fact"].join(transactions.select("id", "accountId"), on="id", how="left"),
            ["accountId"], "amount")
    return [
        ("curated.fact_transaction", lambda: frames["fact"],
         F["FACT_TRANSACTION_RULES"] + [calendar_rule, F["no_dim_account_rule"](dim)], "id"),
        ("curated.fact_transaction vs cleansed", totals, "TOTALS_CONSERVED_RULES", "accountId"),
    ]


def classified_inputs():
    return {"fact": pl.DataFrame({"id": ["t0", "t1", "t2", "t3"],
                                  "netting_applied": [False, False, True, True]})}


def classified_derive(frames):
    live = frames["fact"].filter(~pl.col("netting_applied").fill_null(False))
    return {"classified": live.select("id", subcategoria=pl.lit("Alimentação"))}


def classified_checks(F, frames):
    live = frames["fact"].filter(~pl.col("netting_applied").fill_null(False))
    return [
        ("curated.fact_transaction_classified", lambda: frames["classified"],
         "FACT_TRANSACTION_CLASSIFIED_RULES", "id"),
        ("curated.fact_transaction_classified vs fact_transaction",
         lambda: F["layer_totals"](live, frames["classified"], ["id"]), "ROWS_CARRIED_RULES", "id"),
    ]


def one_check(table, frame_name, rules, key):
    return lambda F, frames: [(table, lambda: frames[frame_name], rules, key)]


# The curated snapshot facts and dim_account, each with the frames its script reconciles it against
# (df-ahua.4.9, df-ahua.4.11). Those source frames are not corrupted: the fact is the frame the script
# builds, and a corruption of it is a wrong write. Each check calls what the script calls.
CREDIT_UUID = "bank0-credit-0002"
SNAPSHOT_DIM = pl.DataFrame({"pluggy_account_id": ["acc0", "acc1"],
                             "account_uuid": ["bank0-checking-0001", CREDIT_UUID]})


def dim_account_inputs():
    return {"dim": DIM_ACCOUNT, "accounts": pl.DataFrame({"id": ["acc0", "acc1"]})}


def dim_account_checks(F, frames):
    # cleansed2curated_accounts.py: the dim, then every cleansed account has its row.
    dim = frames["dim"]
    return [
        ("curated.dim_account", lambda: dim, "DIM_ACCOUNT_RULES", "pluggy_account_id"),
        ("curated.dim_account vs cleansed",
         lambda: F["layer_totals"](frames["accounts"].select(pl.col("id").alias("pluggy_account_id")), dim,
                                   ["pluggy_account_id"]),
         "ROWS_CARRIED_RULES", "pluggy_account_id"),
    ]


def card_statement_inputs():
    fact = pl.DataFrame({
        "account_uuid": [CREDIT_UUID] * 3, "statement_id": ["b0", "b1", "b2"],
        "due_date": [date(2026, m, 20) for m in (1, 2, 3)], "anomes_key": [202601, 202602, 202603],
        "statement_amount": [300.0, 310.0, 320.0], "minimum_payment": [30.0, 31.0, 32.0],
        "paid_amount": [300.0, 310.0, None], "paid_date": [date(2026, 1, 18), date(2026, 2, 18), None],
        "payment_type": ["FULL_PAYMENT", "FULL_PAYMENT", None], "paid_in_full": [True, True, False],
        "finance_charges": [0.0] * 3, "allows_installments": [True] * 3, "currency": ["BRL"] * 3,
        "updated_at": ["2026-01-10T00:00:00.000Z"] * 3})
    # bills: cleansed.bills; this dataset has no re-issued twin, so none is left out.
    bills = pl.DataFrame({"id": ["b0", "b1", "b2"], "accountId": ["acc1"] * 3})
    return {"fact": fact, "bills": bills, "dim": SNAPSHOT_DIM}


def card_statement_checks(F, frames):
    # fact_card_statement.py: the statements, with the dim rule; then every bill but a dropped
    # re-issued twin is a statement.
    twin_ids = []
    return [
        ("curated.fact_card_statement", lambda: frames["fact"],
         F["FACT_CARD_STATEMENT_RULES"] + [F["no_dim_account_rule"](frames["dim"])], "statement_id"),
        ("curated.fact_card_statement vs cleansed",
         lambda: F["layer_totals"](frames["bills"].filter(~pl.col("id").is_in(twin_ids))
                                   .select(pl.col("id").alias("statement_id")), frames["fact"],
                                   ["statement_id"]),
         "ROWS_CARRIED_RULES", "statement_id"),
    ]


def fatura_inputs():
    months = [202601, 202602, 202603]
    fact = pl.DataFrame({
        "account_uuid": [CREDIT_UUID] * 3, "anomes_key": months,
        "mes_inicio": [date(2026, m, 1) for m in (1, 2, 3)],
        "due_date": [date(2026, m, 20) for m in (1, 2, 3)], "statement_id": ["b0", "b1", "b2"],
        "valor_fatura": [300.0, 310.0, 320.0], "valor_pago": [300.0, 310.0, 0.0],
        "valor_pendente": [0.0, 0.0, 320.0], "is_open": [False] * 3})
    statements = pl.DataFrame({"statement_id": ["b0", "b1", "b2"], "anomes_key": months})
    return {"fact": fact, "statements": statements, "dim": SNAPSHOT_DIM}


def fatura_checks(F, frames):
    # fact_fatura_mes.py: the rows, with the dim rule; then every statement with a due month is one
    # closed row.
    stmts, df = frames["statements"], frames["fact"]
    return [
        ("curated.fact_fatura_mes", lambda: df,
         F["FACT_FATURA_MES_RULES"] + [F["no_dim_account_rule"](frames["dim"])], "anomes_key"),
        ("curated.fact_fatura_mes vs fact_card_statement",
         lambda: F["layer_totals"](stmts.filter(pl.col("anomes_key").is_not_null()).select("statement_id"),
                                   df.filter(~pl.col("is_open")).select("statement_id"), ["statement_id"]),
         "ROWS_CARRIED_RULES", "statement_id"),
    ]


def balance_inputs():
    days = _days(date(2026, 1, 10), 3)
    fact = pl.DataFrame({
        "account_uuid": ["bank0-checking-0001"] * 3 + [CREDIT_UUID] * 3, "snapshot_date": days * 2,
        "account_type": ["BANK"] * 3 + ["CREDIT"] * 3, "balance": [100.0, 120.0, 130.0, 50.0, 60.0, 70.0],
        "available": [100.0, 120.0, 130.0, None, None, None],
        "card_outstanding": [None, None, None, 50.0, 60.0, 70.0],
        **{c: pl.Series([None] * 6, dtype=pl.Float64)
           for c in ("available_credit_limit", "credit_limit", "minimum_payment")},
        **{c: pl.Series([None] * 6, dtype=pl.Date) for c in ("statement_close_date", "statement_due_date")},
        "currency": ["BRL"] * 6})
    # accounts: the combined raw.accounts, one row per account and fetch day.
    accounts = pl.DataFrame({"id": ["acc0"] * 3 + ["acc1"] * 3, "ingested_at": [d.isoformat() for d in days] * 2})
    return {"fact": fact, "accounts": accounts, "dim": SNAPSHOT_DIM}


def balance_checks(F, frames):
    # fact_balance_snapshot.py: the rows, with the dim rule; then every raw account row is one row.
    df, dim = frames["fact"], frames["dim"]
    return [
        ("curated.fact_balance_snapshot", lambda: df,
         F["FACT_BALANCE_SNAPSHOT_RULES"] + [F["no_dim_account_rule"](dim)], "snapshot_date"),
        ("curated.fact_balance_snapshot vs raw",
         lambda: F["layer_totals"](frames["accounts"].select(pl.col("id").alias("pluggy_account_id")),
                                   df.join(dim.select("pluggy_account_id", "account_uuid"), on="account_uuid",
                                           how="left"),
                                   ["pluggy_account_id"]),
         "ROWS_CARRIED_RULES", "pluggy_account_id"),
    ]


def investment_inputs():
    fact = pl.DataFrame({
        "holding_uuid": ["h0", "h0", "h1"],
        "snapshot_date": [date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 10)],
        "invested_amount": [1000.0, 1000.0, 500.0], "current_value": [1010.0, 1011.0, 500.0],
        "return": [10.0, 11.0, 0.0], "currency": ["BRL"] * 3})
    # holdings: cleansed.investments inner-joined with dim_investment (the mapped holdings).
    return {"fact": fact, "holdings": fact.select("holding_uuid")}


def investment_checks(F, frames):
    # fact_investment_position.py: the rows; then every mapped holding is one row of this run.
    return [
        ("curated.fact_investment_position", lambda: frames["fact"], "FACT_INVESTMENT_POSITION_RULES",
         "snapshot_date"),
        ("curated.fact_investment_position vs cleansed",
         lambda: F["layer_totals"](frames["holdings"].select("holding_uuid"), frames["fact"], ["holding_uuid"]),
         "ROWS_CARRIED_RULES", "holding_uuid"),
    ]


# --- boundaries -----------------------------------------------------------------------------------

@dataclass
class Boundary:
    name: str
    inputs: object                      # () -> {frame name: good frame}
    checks: object                      # (F, frames) -> [(check, () -> frame, rules or list name, key)]
    shapes: dict                        # frame name -> Shape: the frames corrupted, and their roles
    derive: object = None               # frames -> {derived frame name: frame}, recomputed from inputs
    derived: tuple = field(default=())  # names derive returns

    def frames(self, mutate=None):
        # mutate = (frame name, apply, rng). An input frame is corrupted before the derived frames
        # are computed from it; a derived frame is corrupted after (a wrong write, not a wrong input).
        frames = dict(self.inputs())
        target, apply, rng = mutate or (None, None, None)
        if target in frames:
            frames[target] = apply(frames[target], rng)
        if self.derive:
            try:
                frames.update(self.derive(frames))
            except pl.exceptions.PolarsError as exc:  # the script fails on the same input
                return exc
        if target in self.derived:
            frames[target] = apply(frames[target], rng)
        return frames


RAW_TEXT_SHAPE = dict(partition="ingested_at")
FACT_COLUMNS = [
    "id", "date", "account_uuid", "account_type", "etl_merchant_key", "description_normalized",
    "amount", "amount_signed", "category", "categoryId", "is_transfer", "counterparty_document",
    "payment_method", "cc_bill_id", "cc_installment_number", "cc_total_installments",
    "cc_purchase_date", "status", "txn_type", "operation_type", "cash_flag", "etl_tag",
    "netting_applied", "income_source_key"]

BOUNDARIES = [
    Boundary("raw.transactions", lambda: {"raw": _raw(RAW_TRANSACTION_ROWS)},
             one_check("raw.transactions", "raw", "RAW_TRANSACTIONS_RULES", "id"),
             {"raw": Shape(keys=("id",), amounts=("amount", "amountInAccountCurrency"),
                           dates=("date", "ingested_at"), enums=("type", "status", "currencyCode"),
                           texts=("description", "category"),
                           fks=("accountId",), **RAW_TEXT_SHAPE)}),
    Boundary("raw.accounts", lambda: {"raw": _raw(RAW_ACCOUNT_ROWS)},
             one_check("raw.accounts", "raw", "RAW_ACCOUNTS_RULES", "id"),
             {"raw": Shape(keys=("id",), amounts=("balance",), dates=("ingested_at",),
                           enums=("type", "currencyCode"), texts=("name",), fks=("itemId",),
                           **RAW_TEXT_SHAPE)}),
    Boundary("raw.bills", lambda: {"raw": _raw([raw_bill("b0", 1, D1), raw_bill("b1", 2, D1),
                                                raw_bill("b1", 2, D2), raw_bill("b2", 3, D2)])},
             one_check("raw.bills", "raw", "RAW_BILLS_RULES", "id"),
             {"raw": Shape(keys=("id",), amounts=("totalAmount", "minimumPaymentAmount"),
                           dates=("dueDate", "ingested_at"), enums=("allowsInstallments",),
                           fks=("accountId",), **RAW_TEXT_SHAPE)}),
    Boundary("raw.investments", lambda: {"raw": _raw([raw_investment("h0", D1, "1010.0"),
                                                      raw_investment("h1", D1, "500.0"),
                                                      raw_investment("h0", D2, "1011.0")])},
             one_check("raw.investments", "raw", "RAW_INVESTMENTS_RULES", "id"),
             {"raw": Shape(keys=("id",), amounts=("balance", "amountOriginal"), dates=("ingested_at",),
                           enums=("type",), texts=("name",), **RAW_TEXT_SHAPE)}),
    Boundary("cleansed.transactions", cleansed_transactions_inputs, cleansed_transactions_checks,
             {"new": Shape(keys=("id",), amounts=("amount",), dates=("date", "ingested_at"),
                           enums=("txn_type",), fks=("accountId",), fixed_schema=True),
              "accounts": Shape(enums=("account_type",), fixed_schema=True),
              "written": Shape(keys=("id",), fixed_schema=True)},
             cleansed_transactions_derive, ("written",)),
    Boundary("curated.dim_account", dim_account_inputs, dim_account_checks,
             {"dim": Shape(keys=("pluggy_account_id", "account_uuid"), enums=("type", "currency"),
                           texts=("institution",))}),
    Boundary("curated.fact_transaction", fact_transaction_inputs, fact_transaction_checks,
             {"fact": Shape(keys=("id",), amounts=(("amount", "amount_signed"),), dates=("date",),
                            enums=("account_type", "txn_type", "status"),
                            texts=("description_normalized",), fks=("account_uuid",)),
              "dim_account": Shape(fixed_schema=True),
              "transactions": Shape(amounts=("amount",), fixed_schema=True)},
             lambda frames: fact_transaction_derive(frames, FACT_COLUMNS), ("fact",)),
    Boundary("curated.fact_card_statement", card_statement_inputs, card_statement_checks,
             {"fact": Shape(keys=("statement_id", ("account_uuid", "anomes_key")),
                            amounts=("statement_amount", "paid_amount"), dates=("due_date",),
                            fks=("account_uuid",))}),
    Boundary("curated.fact_fatura_mes", fatura_inputs, fatura_checks,
             {"fact": Shape(keys=(("account_uuid", "anomes_key"),), amounts=("valor_fatura",),
                            dates=("due_date",), fks=("account_uuid",))}),
    Boundary("curated.fact_balance_snapshot", balance_inputs, balance_checks,
             {"fact": Shape(keys=(("account_uuid", "snapshot_date"),), amounts=("balance",),
                            dates=("snapshot_date",), enums=("account_type",), fks=("account_uuid",))}),
    Boundary("curated.fact_investment_position", investment_inputs, investment_checks,
             {"fact": Shape(keys=(("holding_uuid", "snapshot_date"),), amounts=("current_value",),
                            dates=("snapshot_date",))}),
    Boundary("curated.fact_transaction_classified", classified_inputs, classified_checks,
             {"classified": Shape(keys=("id",)), "fact": Shape(fixed_schema=True)},
             classified_derive, ("classified",)),
]


# --- running a boundary's checks -----------------------------------------------------------------

def outcome(F, boundary, frames):
    """The checks of `boundary` run in order through the real validate, as the script runs them: the
    first impossible finding (or error) stops the script. Returns the set of what fired, as
    "impossible: <check>: <rule>", "suspicious: <check>: <rule>" or "error: <exception type>"."""
    if isinstance(frames, Exception):  # building the frames failed: the script fails there too
        return {f"error: {type(frames).__name__}"}
    fired = set()
    for table, frame, rule_list, key in boundary.checks(F, frames):
        rules = F[rule_list] if isinstance(rule_list, str) else rule_list
        try:
            found = F["validate"](frame(), table, rules, key)
        except F["DataValidationError"] as exc:
            fired |= {f"impossible: {table}: {name}" for name in _impossible_rules(str(exc), table, rules)}
            return fired
        except Exception as exc:  # noqa: BLE001 -- any other failure also stops the script before the write
            fired.add(f"error: {type(exc).__name__}")
            return fired
        fired |= {f"suspicious: {table}: {f['rule']}" for f in found}
    return fired


def _impossible_rules(message, table, rules):
    assert message.startswith(f"{table}: ")
    names = []
    for part in message[len(table) + 2:].split("; "):
        match = re.fullmatch(r"(.+?): \d+(?: \(e\.g\. .*\))?", part)
        assert match, part
        names.append(match.group(1))
    known = {r[0] for r in rules if r[1] == "impossible"}
    assert set(names) <= known, names
    return names


def all_cases():
    for b in BOUNDARIES:
        for frame_name, shape in b.shapes.items():
            for case_name, apply in dm.cases(shape):
                yield f"{b.name}/{frame_name}/{case_name}", b, frame_name, apply


CASES = list(all_cases())


# --- the matrix -----------------------------------------------------------------------------------
# For each boundary, what each case does. A fired key lists every rule that fires, in short form:
# "I: <rule>" / "S: <rule>" for the boundary's own check, "I vs raw: <rule>" for the check named
# "<boundary> vs raw", "E: <exception>" when the script's check fails with an error (it stops there,
# before the write, without naming a rule).

@dataclass(frozen=True)
class Tolerated:
    """No rule fires, by design. `same_latest`: the claim is that latest_by_key absorbs it."""
    reason: str
    same_latest: bool = False


@dataclass(frozen=True)
class Hole:
    """No rule fires and corrupt data reaches curated: a strict xfail until the rule lands."""
    bead: str
    reason: str


ABSORBED = Tolerated("latest_by_key keeps one row per id: a repeated row or fetch changes nothing",
                     same_latest=True)
SHORT_FETCH = Tolerated("a fetch with fewer rows looks like a real one; every entity keeps an id's row "
                        "from an earlier fetch (transactions accumulate, the others are latest per id)")
PLAUSIBLE_DATE = Tolerated("still a plausible date: nothing can tell it from the real one")
ANY_NUMBER = Tolerated("any number is a valid value here: nothing to compare it with")
NO_SIGN_RULE = Tolerated("no sign convention is promised for this value (an overdraft, a card credit "
                         "balance are negative)")
SIGN_AT_CLEANSED = Tolerated("raw does not know the account type: cleansed.transactions judges the sign")
# No hole is open today: df-ahua.4.1 to df-ahua.4.11 closed the 90 the matrix found. A new one is
# recorded here as Hole("<bead>", "<what reaches curated>") until its rule lands.

ID_TWICE = "S: id appears twice in one fetch with different content"
PARTITION_DAY = "I: ingested_at is not its partition's day"
RAW_COMMON = {
    ("I: id is null",): ["raw/null_value[id]", "raw/dropped_today[id]"],
    ("I: ingested_at is null",): ["raw/null_value[ingested_at]", "raw/dropped_today[ingested_at]"],
    ("I: ingested_at is not a date", PARTITION_DAY): ["raw/not_parseable[ingested_at]"],
    ("I: ingested_at is after the run day", PARTITION_DAY): ["raw/far_date[ingested_at]"],
    (ID_TWICE,): ["raw/duplicate_key[id]"],
    ABSORBED: ["raw/duplicate_row", "raw/repeat_partition"],
    SHORT_FETCH: ["raw/drop_row", "raw/truncate_partition"],
}
COLUMNS_RULE = "I: columns the script reads are present"
S_COLUMNS_RULE = "S: columns the script reads are present"

OUTCOMES = {
    "raw.transactions": RAW_COMMON | {
        ("I: amount is missing or not a number",): [
            "raw/null_value[amount]", "raw/comma_decimal[amount]", "raw/not_parseable[amount]",
            "raw/dropped_today[amount]"],
        ("I: date is missing or not a date",): [
            "raw/null_value[date]", "raw/not_parseable[date]", "raw/dropped_today[date]"],
        ("I: accountId is null",): ["raw/null_value[accountId]", "raw/dropped_today[accountId]"],
        (PARTITION_DAY,): ["raw/shift_date[ingested_at]"],
        ("S: type is not DEBIT or CREDIT",): ["raw/unknown_enum[type]"],
        ("S: status is not POSTED or PENDING",): ["raw/unknown_enum[status]"],
        ("S: type is null",): ["raw/null_value[type]", "raw/missing_column[type]", "raw/dropped_today[type]"],
        ("S: status is null",): ["raw/null_value[status]", "raw/missing_column[status]",
                                 "raw/dropped_today[status]"],
        ("S: date is more than a year after ingested_at or before 2000",): ["raw/far_date[date]"],
        ("S: description has broken encoding",): ["raw/broken_encoding[description]"],
        ("S: category has broken encoding",): ["raw/broken_encoding[category]"],
        ("S: description is null on every row of a fetch",): ["raw/dropped_today[description]"],
        ("S: category is null on every row of a fetch",): ["raw/dropped_today[category]"],
        (COLUMNS_RULE, "I: id is null"): ["raw/missing_column[id]"],
        (COLUMNS_RULE, "I: amount is missing or not a number"): ["raw/missing_column[amount]"],
        (COLUMNS_RULE, "I: date is missing or not a date"): ["raw/missing_column[date]"],
        (COLUMNS_RULE, "I: ingested_at is null"): ["raw/missing_column[ingested_at]"],
        (COLUMNS_RULE, "I: accountId is null"): ["raw/missing_column[accountId]"],
        (COLUMNS_RULE,): ["raw/missing_column[description]", "raw/missing_column[category]"],
        Tolerated("nothing to merge: an empty source never loses history (A11)"): ["raw/empty_input"],
        # df-ahua.3.1: the foreign-currency row (t5) loses the BRL value the bank converted, or another
        # currency shows up without one: the foreign number would be summed as BRL.
        ("S: currencyCode is not BRL and amountInAccountCurrency is null",): [
            "raw/null_value[amountInAccountCurrency]", "raw/missing_column[amountInAccountCurrency]",
            "raw/dropped_today[amountInAccountCurrency]", "raw/unknown_enum[currencyCode]"],
        ("I: amountInAccountCurrency is not a number",): [
            "raw/comma_decimal[amountInAccountCurrency]", "raw/not_parseable[amountInAccountCurrency]"],
        Tolerated("the amount cleansed takes does not depend on currencyCode: the converted value is "
                  "taken when set, and a row without a currency reads as the account's (BRL)"): [
            "raw/null_value[currencyCode]", "raw/missing_column[currencyCode]",
            "raw/dropped_today[currencyCode]"],
        SIGN_AT_CLEANSED: ["raw/flip_sign[amount]", "raw/flip_sign[amountInAccountCurrency]"],
        ANY_NUMBER: ["raw/nudge_amount[amount]", "raw/nudge_amount[amountInAccountCurrency]"],
        PLAUSIBLE_DATE: ["raw/shift_date[date]"],
        Tolerated("raw has no accounts to resolve it against: cleansed.transactions refuses it "
                  "(accountId matches no raw account)"): ["raw/dangling_fk[accountId]"],
    },
    "raw.accounts": RAW_COMMON | {
        ("S: balance is null",): ["raw/null_value[balance]", "raw/dropped_today[balance]"],
        ("S: itemId is null",): ["raw/null_value[itemId]", "raw/dropped_today[itemId]"],
        ("I: balance is not a number",): ["raw/comma_decimal[balance]", "raw/not_parseable[balance]"],
        ("S: type is not BANK or CREDIT",): ["raw/unknown_enum[type]"],
        ("S: currencyCode is not BRL",): ["raw/null_value[currencyCode]", "raw/unknown_enum[currencyCode]",
                                          "raw/dropped_today[currencyCode]"],
        (PARTITION_DAY,): ["raw/shift_date[ingested_at]"],
        (COLUMNS_RULE, "I: id is null"): ["raw/missing_column[id]"],
        (COLUMNS_RULE, "I: ingested_at is null"): ["raw/missing_column[ingested_at]"],
        (COLUMNS_RULE,): ["raw/missing_column[balance]", "raw/missing_column[type]",
                          "raw/missing_column[currencyCode]", "raw/missing_column[name]",
                          "raw/missing_column[itemId]"],
        Tolerated("once it is the account's latest row, cleansed.transactions refuses the account's "
                  "transactions (accountId matches no raw account)"): [
            "raw/null_value[type]", "raw/dropped_today[type]"],
        Tolerated("with no account at all, cleansed.transactions refuses every transaction of the run "
                  "(accountId matches no raw account)"): ["raw/empty_input"],
        NO_SIGN_RULE: ["raw/flip_sign[balance]"],
        ANY_NUMBER: ["raw/nudge_amount[balance]"],
        Tolerated("the raw account name stops at cleansed.accounts: dim_account names the account "
                  "from institutions.yaml"): ["raw/broken_encoding[name]", "raw/dropped_today[name]"],
        Tolerated("itemId only picks the institution; dim_account then flags it (suspicious: "
                  "institution is a placeholder)"): ["raw/dangling_fk[itemId]"],
    },
    "raw.bills": RAW_COMMON | {
        ("I: id is null",): ["raw/null_value[id]", "raw/missing_column[id]", "raw/dropped_today[id]"],
        ("I: ingested_at is null",): ["raw/null_value[ingested_at]", "raw/missing_column[ingested_at]",
                                      "raw/dropped_today[ingested_at]"],
        (PARTITION_DAY,): ["raw/shift_date[ingested_at]"],
        ("S: totalAmount is null",): ["raw/null_value[totalAmount]", "raw/dropped_today[totalAmount]"],
        (S_COLUMNS_RULE, "S: totalAmount is null"): ["raw/missing_column[totalAmount]"],
        ("S: dueDate is null",): ["raw/null_value[dueDate]", "raw/dropped_today[dueDate]"],
        (S_COLUMNS_RULE, "S: dueDate is null"): ["raw/missing_column[dueDate]"],
        ("I: accountId is null",): ["raw/null_value[accountId]", "raw/missing_column[accountId]",
                                    "raw/dropped_today[accountId]"],
        ("I: totalAmount is not a number",): ["raw/comma_decimal[totalAmount]",
                                              "raw/not_parseable[totalAmount]"],
        ("I: minimumPaymentAmount is not a number",): ["raw/comma_decimal[minimumPaymentAmount]",
                                                       "raw/not_parseable[minimumPaymentAmount]"],
        ("I: dueDate is not a date",): ["raw/not_parseable[dueDate]"],
        ("S: dueDate is more than a year after ingested_at or before 2000",): ["raw/far_date[dueDate]"],
        ("S: allowsInstallments is not True or False",): ["raw/unknown_enum[allowsInstallments]"],
        ("S: minimumPaymentAmount is null on every row of a fetch",): [
            "raw/dropped_today[minimumPaymentAmount]"],
        (S_COLUMNS_RULE, "S: minimumPaymentAmount is null on every row of a fetch"): [
            "raw/missing_column[minimumPaymentAmount]"],
        ("S: allowsInstallments is null on every row of a fetch",): ["raw/dropped_today[allowsInstallments]"],
        (S_COLUMNS_RULE, "S: allowsInstallments is null on every row of a fetch"): [
            "raw/missing_column[allowsInstallments]"],
        Tolerated("optional display fields: cleansed carries the null (allowsInstallments is derived "
                  "null-safe)"): ["raw/null_value[minimumPaymentAmount]", "raw/null_value[allowsInstallments]"],
        Tolerated("a card with no closed bill is legal"): ["raw/empty_input"],
        NO_SIGN_RULE: ["raw/flip_sign[totalAmount]", "raw/flip_sign[minimumPaymentAmount]"],
        ANY_NUMBER: ["raw/nudge_amount[totalAmount]", "raw/nudge_amount[minimumPaymentAmount]"],
        PLAUSIBLE_DATE: ["raw/shift_date[dueDate]"],
        Tolerated("raw has no accounts to resolve it against; fact_card_statement refuses the bill its "
                  "inner join drops (vs cleansed: rows not carried one to one)"): ["raw/dangling_fk[accountId]"],
    },
    "raw.investments": RAW_COMMON | {
        ("I: id is null",): ["raw/null_value[id]", "raw/missing_column[id]", "raw/dropped_today[id]"],
        ("I: ingested_at is null",): ["raw/null_value[ingested_at]", "raw/missing_column[ingested_at]",
                                      "raw/dropped_today[ingested_at]"],
        # The shifted row lands on the day of the holding's other fetch, so it is two different rows
        # for one id too; that warning is not recorded once the impossible rule stops the run.
        (PARTITION_DAY,): ["raw/shift_date[ingested_at]"],
        ("S: balance is null",): ["raw/null_value[balance]", "raw/dropped_today[balance]"],
        ("S: balance is null", S_COLUMNS_RULE): ["raw/missing_column[balance]"],
        ("I: balance is not a number",): ["raw/comma_decimal[balance]", "raw/not_parseable[balance]"],
        ("I: amountOriginal is not a number",): ["raw/comma_decimal[amountOriginal]",
                                                 "raw/not_parseable[amountOriginal]"],
        ("S: type is not a Pluggy investment type",): ["raw/unknown_enum[type]"],
        ("S: name has broken encoding",): ["raw/broken_encoding[name]"],
        ("S: amountOriginal is null on every row of a fetch",): ["raw/dropped_today[amountOriginal]"],
        ("S: type is null on every row of a fetch",): ["raw/dropped_today[type]"],
        ("S: name is null on every row of a fetch",): ["raw/dropped_today[name]"],
        ("S: amountOriginal is null on every row of a fetch", S_COLUMNS_RULE): [
            "raw/missing_column[amountOriginal]"],
        (S_COLUMNS_RULE, "S: type is null on every row of a fetch"): ["raw/missing_column[type]"],
        (S_COLUMNS_RULE, "S: name is null on every row of a fetch"): ["raw/missing_column[name]"],
        Tolerated("optional for one holding: the list requires only balance"): [
            "raw/null_value[amountOriginal]", "raw/null_value[type]"],
        Tolerated("an item with no investments is legal (the clean seam of raw2cleansed_investments)"): [
            "raw/empty_input"],
        Tolerated("no sign convention at raw; fact_investment_position flags a negative current_value "
                  "(suspicious)"): ["raw/flip_sign[balance]", "raw/flip_sign[amountOriginal]"],
        ANY_NUMBER: ["raw/nudge_amount[balance]", "raw/nudge_amount[amountOriginal]"],
    },
    # `new` is new_rows: a corruption of it is a row going wrong between latest_by_key and the MERGE,
    # which the check against txn_latest ("vs raw") catches (df-ahua.4.8).
    "cleansed.transactions": {
        ("I: accountId matches no raw account",): [
            "new/null_value[accountId]", "new/dangling_fk[accountId]", "accounts/null_value[account_type]",
            "accounts/drop_row", "accounts/empty_input"],
        ("I vs raw: id written more than once",): [
            "new/duplicate_row", "accounts/duplicate_row", "written/duplicate_row"],
        ("I: amount sign contradicts account and transaction type",): ["new/flip_sign[amount]"],
        ("I vs raw: id written more than once", "I vs raw: rows lost"): [
            "new/duplicate_key[id]", "written/duplicate_key[id]"],
        ("I vs raw: rows lost",): ["new/drop_row", "new/empty_input", "written/drop_row",
                                   "written/empty_input"],
        ("I vs last run: rows lost",): ["written/null_value[id]"],
        Tolerated("refused upstream by the raw rules (id, amount, date, ingested_at must be set)"): [
            "new/null_value[id]", "new/null_value[amount]", "new/null_value[date]",
            "new/null_value[ingested_at]"],
        Tolerated("flagged upstream (suspicious): raw.transactions 'type is null'"): [
            "new/null_value[txn_type]"],
        Tolerated("flagged upstream (suspicious): raw.transactions 'type is not DEBIT or CREDIT'"): [
            "new/unknown_enum[txn_type]"],
        Tolerated("flagged upstream (suspicious): raw.accounts 'type is not BANK or CREDIT'"): [
            "accounts/unknown_enum[account_type]"],
        ANY_NUMBER: ["new/nudge_amount[amount]"],
        PLAUSIBLE_DATE: ["new/shift_date[date]", "new/shift_date[ingested_at]"],
        Tolerated("flagged upstream (suspicious): raw.transactions 'date is more than a year after "
                  "ingested_at or before 2000'"): ["new/far_date[date]"],
        Tolerated("refused upstream: raw.transactions 'ingested_at is after the run day'"): [
            "new/far_date[ingested_at]"],
    },
    "curated.dim_account": {
        ("I: pluggy_account_id is null",): ["dim/null_value[pluggy_account_id]",
                                            "dim/missing_column[pluggy_account_id]"],
        ("I: account_uuid is null",): ["dim/null_value[account_uuid]", "dim/missing_column[account_uuid]"],
        ("I: pluggy_account_id is not unique",): ["dim/duplicate_key[pluggy_account_id]"],
        ("I: account_uuid is not unique",): ["dim/duplicate_key[account_uuid]"],
        ("I: account_uuid is not unique", "I: pluggy_account_id is not unique"): ["dim/duplicate_row"],
        ("I vs cleansed: rows not carried one to one",): ["dim/drop_row", "dim/empty_input"],
        ("S: institution has broken encoding",): ["dim/broken_encoding[institution]"],
        Tolerated("display column: no reader in curated or the report today (they read account_uuid "
                  "and pluggy_account_id)"): [
            "dim/null_value[type]", "dim/null_value[currency]", "dim/unknown_enum[type]",
            "dim/unknown_enum[currency]", "dim/missing_column[type]", "dim/missing_column[currency]",
            "dim/missing_column[institution]"],
    },
    "curated.fact_transaction": {
        ("I: id is null",): ["fact/null_value[id]"],
        ("I: amount is null",): ["fact/null_value[amount+amount_signed]", "transactions/null_value[amount]"],
        ("I: date is null",): ["fact/null_value[date]"],
        ("I: account_uuid is null",): ["fact/null_value[account_uuid]"],
        ("I: account_uuid has no dim_account row",): ["fact/dangling_fk[account_uuid]"],
        ("I: amount_signed is not amount (BANK) or -amount (CREDIT)",): [
            "fact/null_value[account_type]", "fact/nudge_amount[amount+amount_signed]"],
        ("I: id is not unique",): ["fact/duplicate_key[id]", "fact/duplicate_row", "dim_account/duplicate_row",
                                   "transactions/duplicate_row"],
        # dim_account has every cleansed account (D3): a transaction the inner join drops is lost.
        ("I vs cleansed: amount total not conserved", "I vs cleansed: rows not carried one to one"): [
            "fact/drop_row", "fact/empty_input", "dim_account/drop_row", "dim_account/empty_input"],
        ("I vs cleansed: amount total not conserved",): ["fact/flip_sign[amount+amount_signed]"],
        ("E: InvalidOperationError",): ["fact/comma_decimal[amount+amount_signed]",
                                        "fact/not_parseable[amount+amount_signed]", "fact/not_parseable[date]"],
        ("S: date has no row in dim_calendar",): ["fact/far_date[date]"],
        ("I: expected columns are present", "I: id is null"): ["fact/missing_column[id]"],
        ("I: amount is null", "I: amount_signed is not amount (BANK) or -amount (CREDIT)",
         "I: expected columns are present"): ["fact/missing_column[amount]"],
        ("I: amount_signed is not amount (BANK) or -amount (CREDIT)", "I: expected columns are present"): [
            "fact/missing_column[amount_signed]", "fact/missing_column[account_type]"],
        ("I: date is null", "I: expected columns are present"): ["fact/missing_column[date]"],
        ("I: account_uuid is null", "I: expected columns are present"): ["fact/missing_column[account_uuid]"],
        ("I: expected columns are present",): ["fact/missing_column[txn_type]", "fact/missing_column[status]",
                                               "fact/missing_column[description_normalized]"],
        Tolerated("carried from cleansed; flagged upstream (suspicious): raw.transactions 'type is null', "
                  "'status is null'"): ["fact/null_value[txn_type]", "fact/null_value[status]"],
        Tolerated("flagged upstream (suspicious) by the raw enum rules"): [
            "fact/unknown_enum[account_type]", "fact/unknown_enum[txn_type]", "fact/unknown_enum[status]"],
        Tolerated("carried from raw; flagged upstream (suspicious): raw.transactions 'description has "
                  "broken encoding'"): ["fact/broken_encoding[description_normalized]"],
        PLAUSIBLE_DATE: ["fact/shift_date[date]"],
        Tolerated("a row lost in cleansed is refused there (cleansed.transactions vs last run: rows lost)"): [
            "transactions/drop_row", "transactions/empty_input"],
        Tolerated("refused at cleansed.transactions (amount sign contradicts account and transaction type)"): [
            "transactions/flip_sign[amount]"],
        ANY_NUMBER: ["transactions/nudge_amount[amount]"],
    },
    "curated.fact_card_statement": {
        ("I: statement_id is null",): ["fact/null_value[statement_id]"],
        ("I: expected columns are present", "I: statement_id is null"): ["fact/missing_column[statement_id]"],
        ("I: account_uuid is null",): ["fact/null_value[account_uuid+anomes_key]", "fact/null_value[account_uuid]"],
        ("I: account_uuid is null", "I: expected columns are present"): ["fact/missing_column[account_uuid]"],
        ("I: account_uuid has no dim_account row",): ["fact/dangling_fk[account_uuid]"],
        ("S: due_date is null",): ["fact/null_value[due_date]"],
        ("S: statement_amount is null",): ["fact/null_value[statement_amount]"],
        ("I: statement_id is not unique",): ["fact/duplicate_key[statement_id]"],
        ("I: more than one statement per account and due month",): ["fact/duplicate_key[account_uuid+anomes_key]"],
        ("I: more than one statement per account and due month", "I: statement_id is not unique"): [
            "fact/duplicate_row"],
        ("I vs cleansed: rows not carried one to one",): ["fact/drop_row", "fact/empty_input"],
        ("I: statement_amount is not a number",): ["fact/comma_decimal[statement_amount]",
                                                   "fact/not_parseable[statement_amount]"],
        ("I: paid_amount is not a number",): ["fact/comma_decimal[paid_amount]", "fact/not_parseable[paid_amount]"],
        ("I: due_date is not a date",): ["fact/not_parseable[due_date]"],
        ("S: due_date is more than a year after the run day or before 2000",): ["fact/far_date[due_date]"],
        ("I: expected columns are present",): [
            "fact/missing_column[anomes_key]", "fact/missing_column[statement_amount]",
            "fact/missing_column[paid_amount]", "fact/missing_column[due_date]"],
        Tolerated("an unpaid statement has no paid_amount"): ["fact/null_value[paid_amount]"],
        NO_SIGN_RULE: ["fact/flip_sign[statement_amount]", "fact/flip_sign[paid_amount]"],
        ANY_NUMBER: ["fact/nudge_amount[statement_amount]", "fact/nudge_amount[paid_amount]"],
        PLAUSIBLE_DATE: ["fact/shift_date[due_date]"],
    },
    "curated.fact_fatura_mes": {
        ("I: account_uuid is null", "I: anomes_key is null"): ["fact/null_value[account_uuid+anomes_key]"],
        ("I: account_uuid is null",): ["fact/null_value[account_uuid]"],
        ("I: account_uuid is null", "I: expected columns are present"): ["fact/missing_column[account_uuid]"],
        ("I: anomes_key is null", "I: expected columns are present"): ["fact/missing_column[anomes_key]"],
        ("I: account_uuid has no dim_account row",): ["fact/dangling_fk[account_uuid]"],
        ("I: more than one row per account and due month",): ["fact/duplicate_key[account_uuid+anomes_key]",
                                                              "fact/duplicate_row"],
        ("I vs fact_card_statement: rows not carried one to one",): ["fact/drop_row", "fact/empty_input"],
        ("S: valor_fatura is null",): ["fact/null_value[valor_fatura]"],
        ("S: due_date is null on a closed bill",): ["fact/null_value[due_date]"],
        ("I: valor_fatura is not a number",): ["fact/comma_decimal[valor_fatura]",
                                               "fact/not_parseable[valor_fatura]"],
        ("I: due_date is not a date",): ["fact/not_parseable[due_date]"],
        ("S: due_date is more than a year after the run day or before 2000",): ["fact/far_date[due_date]"],
        ("I: expected columns are present",): ["fact/missing_column[valor_fatura]", "fact/missing_column[due_date]"],
        NO_SIGN_RULE: ["fact/flip_sign[valor_fatura]"],
        ANY_NUMBER: ["fact/nudge_amount[valor_fatura]"],
        PLAUSIBLE_DATE: ["fact/shift_date[due_date]"],
    },
    "curated.fact_balance_snapshot": {
        ("I: account_uuid is null", "I: snapshot_date is null"): ["fact/null_value[account_uuid+snapshot_date]"],
        ("I: snapshot_date is null",): ["fact/null_value[snapshot_date]"],
        ("I: expected columns are present", "I: snapshot_date is null"): ["fact/missing_column[snapshot_date]"],
        ("I: account_uuid is null",): ["fact/null_value[account_uuid]"],
        ("I: account_uuid is null", "I: expected columns are present"): ["fact/missing_column[account_uuid]"],
        ("I: account_uuid has no dim_account row",): ["fact/dangling_fk[account_uuid]"],
        ("I: more than one row per account and day",): ["fact/duplicate_key[account_uuid+snapshot_date]",
                                                        "fact/duplicate_row"],
        ("I vs raw: rows not carried one to one",): ["fact/drop_row", "fact/empty_input"],
        ("S: balance is null",): ["fact/null_value[balance]"],
        ("S: account_type is null",): ["fact/null_value[account_type]"],
        ("I: balance is not a number",): ["fact/comma_decimal[balance]", "fact/not_parseable[balance]"],
        ("I: snapshot_date is not a date",): ["fact/not_parseable[snapshot_date]"],
        ("I: snapshot_date is after the run day",): ["fact/far_date[snapshot_date]"],
        ("I: expected columns are present",): ["fact/missing_column[balance]", "fact/missing_column[account_type]"],
        NO_SIGN_RULE: ["fact/flip_sign[balance]"],
        ANY_NUMBER: ["fact/nudge_amount[balance]"],
        PLAUSIBLE_DATE: ["fact/shift_date[snapshot_date]"],
        Tolerated("flagged upstream (suspicious): raw.accounts 'type is not BANK or CREDIT'"): [
            "fact/unknown_enum[account_type]"],
    },
    "curated.fact_investment_position": {
        ("I: holding_uuid is null", "I: snapshot_date is null"): ["fact/null_value[holding_uuid+snapshot_date]"],
        ("I: snapshot_date is null",): ["fact/null_value[snapshot_date]"],
        ("I: expected columns are present", "I: snapshot_date is null"): ["fact/missing_column[snapshot_date]"],
        ("I: expected columns are present", "I: holding_uuid is null"): ["fact/missing_column[holding_uuid]"],
        ("I: more than one row per holding and day",): ["fact/duplicate_key[holding_uuid+snapshot_date]",
                                                        "fact/duplicate_row"],
        ("I vs cleansed: rows not carried one to one",): ["fact/drop_row", "fact/empty_input"],
        ("S: current_value is null",): ["fact/null_value[current_value]"],
        ("S: current_value is negative",): ["fact/flip_sign[current_value]"],
        ("I: current_value is not a number",): ["fact/comma_decimal[current_value]",
                                                "fact/not_parseable[current_value]"],
        ("I: snapshot_date is not a date",): ["fact/not_parseable[snapshot_date]"],
        ("I: snapshot_date is after the run day",): ["fact/far_date[snapshot_date]"],
        ("I: expected columns are present",): ["fact/missing_column[current_value]"],
        ANY_NUMBER: ["fact/nudge_amount[current_value]"],
        PLAUSIBLE_DATE: ["fact/shift_date[snapshot_date]"],
    },
    "curated.fact_transaction_classified": {
        ("I: id is null",): ["classified/null_value[id]", "classified/missing_column[id]"],
        ("I: id is not unique",): ["classified/duplicate_key[id]", "classified/duplicate_row"],
        ("I vs fact_transaction: rows not carried one to one",): ["classified/drop_row",
                                                                 "classified/empty_input"],
        Tolerated("fact_transaction is checked before classify runs (id is not unique; vs cleansed)"): [
            "fact/duplicate_row", "fact/drop_row", "fact/empty_input"],
    },
}

SEVERITY = {"I": "impossible", "S": "suspicious"}


def _expand(boundary, short):
    # "I vs raw: rows lost" -> "impossible: <boundary> vs raw: rows lost"; "E: X" -> "error: X".
    head, rule = short.split(": ", 1)
    if head == "E":
        return f"error: {rule}"
    return f"{SEVERITY[head[0]]}: {boundary}{head[1:]}: {rule}"


def expected(case_id):
    boundary, case = case_id.split("/", 1)
    hits = [k for k, cases in OUTCOMES[boundary].items() if case in cases]
    assert len(hits) == 1, f"{case_id}: {len(hits)} outcomes in OUTCOMES"
    outcome_ = hits[0]
    if isinstance(outcome_, tuple):
        return {_expand(boundary, s) for s in outcome_}
    return outcome_


def _marks(case_id):
    want = expected(case_id)
    if isinstance(want, Hole):
        return [pytest.mark.xfail(strict=True, reason=f"{want.bead}: {want.reason}")]
    return []


@pytest.mark.parametrize("boundary", BOUNDARIES, ids=[b.name for b in BOUNDARIES])
def test_good_dataset_passes_every_check(F, boundary):
    # A mutation of a dataset that already fails proves nothing.
    assert outcome(F, boundary, boundary.frames()) == set()


@pytest.mark.parametrize("case_id,boundary,frame_name,apply",
                         [pytest.param(*c, id=c[0], marks=_marks(c[0])) for c in CASES])
def test_mutation(F, case_id, boundary, frame_name, apply):
    good = boundary.frames()
    frames = boundary.frames((frame_name, apply, dm.rng_for(case_id)))
    if not isinstance(frames, Exception):
        assert not frames[frame_name].equals(good[frame_name]), "the mutation changed nothing"
    want = expected(case_id)
    fired = outcome(F, boundary, frames)
    if isinstance(want, set):
        assert fired == want
        return
    if isinstance(want, Hole):
        assert fired, f"no rule notices it: {want.reason}"  # xfail until the bead's rule lands
        return
    assert fired == set()
    if want.same_latest:
        def latest(df):
            return F["latest_by_key"]([df], ["id"], "ingested_at").sort("id")
        assert latest(frames[frame_name]).equals(latest(good[frame_name]))


# Every case has exactly one outcome and every outcome names real cases: a new operator or role
# cannot land without deciding what it means here.
def test_every_case_has_one_outcome_and_every_outcome_a_case():
    ids = {c[0] for c in CASES}
    listed = [f"{b}/{case}" for b, table in OUTCOMES.items() for cases in table.values() for case in cases]
    assert sorted(set(listed)) == sorted(listed), "a case is listed twice"
    assert set(listed) == ids


def test_the_mutator_is_deterministic():
    for case_id, boundary, frame_name, apply in CASES[::7]:
        a = boundary.frames((frame_name, apply, dm.rng_for(case_id)))
        b = boundary.frames((frame_name, apply, dm.rng_for(case_id)))
        if not isinstance(a, Exception):
            assert a[frame_name].equals(b[frame_name]), case_id
