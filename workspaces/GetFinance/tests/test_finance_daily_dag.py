"""Contract test for the finance_daily job DAG (U9 / df-3t6).

Pins the *wiring* of jobs/finance_daily/job.yaml after the per-entity fan-out.
Loads the real job.yaml (not a synthetic fixture) and asserts the properties the
wiring must hold, so a future edit that breaks a branch — or reintroduces an
unsafe concurrent write — fails here instead of in production.

Two non-obvious constraints are encoded:

1. SINGLE-WRITER SAFETY. Each medallion layer is a single DuckDB file
   (cleansed.db, curated.db) and DuckDB locks a file to one read-write process.
   The engine runs every task in a topological *level* concurrently (subprocess
   each), so two tasks that write the same .db file must never share a level.
   test_no_two_duckdb_writers_share_a_level encodes exactly that invariant —
   independent of the precise ordering, so a later cross-file optimisation that
   stays safe still passes. (True parallel branches await the parquet-storage
   epic; see df-<filed>.)

2. CROSS-BRANCH EDGE. The transactions `curate` step (cleansed2curated.py ->
   fact_transaction) inner-joins curated.dim_account to resolve account_uuid
   (U8). It must therefore run AFTER dim_accounts, or the join matches nothing
   and every transaction is silently dropped.
"""

from pathlib import Path

import yaml

from dataforge.dag import topological_levels
from dataforge.parser import load_job

# The job definitions of this checkout (the workspace root is the repo root).
WORKSPACE_DIR = Path(__file__).resolve().parent.parent


def _load():
    return load_job(WORKSPACE_DIR, "finance_daily")


def _names(tasks):
    return {t.name for t in tasks}


def _by_name(job):
    return {t.name: t for t in job.tasks}


def _level_index(job):
    """task name -> index of the topological level it runs in."""
    levels = topological_levels(job.tasks)
    return {t.name: i for i, level in enumerate(levels) for t in level}


def _duckdb_connections():
    cfg = yaml.safe_load((WORKSPACE_DIR / "connections" / "connections.yaml").read_text())
    return {name for name, c in cfg["connections"].items() if c.get("type") == "duckdb"}


def test_job_loads_and_has_all_branches():
    job = _load()
    expected = {
        "fetch",
        "calendar",
        "transform",
        "transform_accounts",
        "transform_investments",
        "transform_bills",
        "dim_accounts",
        "dim_investments",
        "curate",
        "dim_counterparty",
        "fact_balance",
        "fact_investments",
        "fact_card_statement",
        "fact_fatura_mes",
        "classify",
        "report",
        "conferencia_saldo",
        "lancamentos_suspeitos",
        "extrato",
        "veredito_suspeitos",
        "conferencia_fatura",
        # The data checks and the failure path (df-ahua.3); when each one runs is tested in
        # test_job_wiring.py.
        "data_warnings",
        "alert_warnings",
        "alert_failure",
        "job_failed",
    }
    assert _names(job.tasks) == expected


def test_no_two_duckdb_writers_share_a_level():
    """The core safety invariant: no DuckDB file gets two concurrent writers.

    Within any topological level (run concurrently by the engine), at most one
    task may target a given duckdb-typed connection.
    """
    job = _load()
    duckdb_conns = _duckdb_connections()
    for i, level in enumerate(topological_levels(job.tasks)):
        for conn in duckdb_conns:
            writers = [t.name for t in level if t.params.get("target") == conn]
            assert len(writers) <= 1, (
                f"level {i} has concurrent writers {writers} to duckdb '{conn}' "
                f"— DuckDB locks the file to one process"
            )


def test_curate_runs_after_dim_accounts_cross_branch():
    # fact_transaction inner-joins curated.dim_account (U8): curate must not run
    # before dim_accounts, or it drops every transaction.
    lvl = _level_index(_load())
    assert lvl["dim_accounts"] < lvl["curate"]


def test_facts_run_after_their_dim_builder():
    lvl = _level_index(_load())
    assert lvl["dim_accounts"] < lvl["fact_balance"]
    assert lvl["dim_investments"] < lvl["fact_investments"]


def test_dim_counterparty_runs_after_transform_and_curate():
    # dim_counterparty reads cleansed.transactions (transform) and is serialized after the
    # curated writer (curate) so the curated layer never has two concurrent writers (df-pzu).
    lvl = _level_index(_load())
    assert lvl["transform"] < lvl["dim_counterparty"]
    assert lvl["curate"] < lvl["dim_counterparty"]


def test_dims_run_after_their_cleansed_builder():
    lvl = _level_index(_load())
    assert lvl["transform_accounts"] < lvl["dim_accounts"]
    assert lvl["transform_investments"] < lvl["dim_investments"]


def test_everything_runs_after_fetch_except_calendar():
    job = _load()
    lvl = _level_index(job)
    assert lvl["fetch"] == 0
    for name in _names(job.tasks):
        if name in ("fetch", "calendar"):
            continue
        assert lvl[name] > lvl["fetch"], f"{name} must depend on fetch"


def test_calendar_is_independent_and_not_a_duckdb_writer():
    # calendar regenerates dim_calendar.parquet (plain parquet) — it never
    # touches the .db files, so it can float without contention.
    tasks = _by_name(_load())
    cal = tasks["calendar"]
    assert cal.depends_on == []
    assert cal.params.get("target") not in _duckdb_connections()


def test_export_task_retired():
    job = _load()
    assert "export" not in _names(job.tasks)
    for t in job.tasks:
        assert "curated2excel" not in (t.script or "")


def test_every_referenced_script_exists_on_disk():
    job = _load()
    for t in job.tasks:
        if t.script:
            # task.script is bare (relative to scripts/); resolves at WORKSPACE_DIR/scripts/<bare>.
            assert (WORKSPACE_DIR / "scripts" / t.script).exists(), f"{t.name}: missing {t.script}"


def test_fact_fatura_mes_wired_correctly():
    """fact_fatura_mes runs after curate, fact_card_statement, and dim_counterparty."""
    job = _load()
    lvl = _level_index(job)
    assert "fact_fatura_mes" in lvl, "fact_fatura_mes missing from job"
    assert lvl["curate"] < lvl["fact_fatura_mes"]
    assert lvl["fact_card_statement"] < lvl["fact_fatura_mes"]
    assert lvl["dim_counterparty"] < lvl["fact_fatura_mes"]
    task = _by_name(job)["fact_fatura_mes"]
    assert task.script == "cleansed2curated/fact_fatura_mes.py"
    assert task.params.get("source") == "cleansed"
    assert task.params.get("target") == "curated"


def test_classify_and_report_run_after_what_they_read():
    """classify reads fact_transaction and the fatura months; report aggregates the classified
    transactions with balances, investments and faturas (df-cns). Both write curated, so they also
    stay off the levels of the other curated writers (see the single-writer test)."""
    lvl = _level_index(_load())
    assert lvl["curate"] < lvl["classify"]
    assert lvl["fact_fatura_mes"] < lvl["classify"]
    for upstream in ("classify", "fact_balance", "fact_investments", "fact_fatura_mes"):
        assert lvl[upstream] < lvl["report"], f"report must run after {upstream}"


def test_conferencia_saldo_runs_after_what_it_reads_and_before_the_warnings():
    """conferencia_saldo reads raw.accounts, cleansed.transactions and curated.dim_account (df-z4fh.1),
    is serialized after the last curated writer (report), and records its finding before data_warnings
    reads the findings."""
    lvl = _level_index(_load())
    for upstream in ("transform", "fact_balance", "report"):
        assert lvl[upstream] < lvl["conferencia_saldo"], f"conferencia_saldo must run after {upstream}"
    assert lvl["conferencia_saldo"] < lvl["data_warnings"]


def test_lancamentos_suspeitos_runs_after_the_balance_check_and_before_the_warnings():
    """lancamentos_suspeitos reads curated.conferencia_saldo, cleansed.transactions and raw.transactions
    (df-z4fh.2), and runs before data_warnings, which keeps its `when: completed` edge on the balance check."""
    job = _load()
    lvl = _level_index(job)
    assert lvl["conferencia_saldo"] < lvl["lancamentos_suspeitos"] < lvl["data_warnings"]
    edges = {e.task: e.when for e in _by_name(job)["data_warnings"].depends_on}
    assert edges["conferencia_saldo"] == ["succeeded", "failed"]


def test_extrato_runs_after_the_last_curated_writer_and_before_the_warnings():
    """extrato reads cleansed.accounts and curated.dim_account (df-z4fh.5), is serialized after the last
    curated writer (lancamentos_suspeitos, whatever its outcome) and runs before data_warnings, whose other
    edges stay as they were."""
    job = _load()
    lvl = _level_index(job)
    assert lvl["dim_accounts"] < lvl["extrato"]
    assert lvl["lancamentos_suspeitos"] < lvl["extrato"] < lvl["data_warnings"]
    task = _by_name(job)["extrato"]
    assert task.params == {"source": "extratos", "target": "curated"}
    assert {e.task: e.when for e in task.depends_on} == {
        "dim_accounts": ["succeeded"], "lancamentos_suspeitos": ["succeeded", "failed", "skipped"]}
    edges = {e.task: e.when for e in _by_name(job)["data_warnings"].depends_on}
    assert edges["conferencia_saldo"] == ["succeeded", "failed"]
    assert edges["lancamentos_suspeitos"] == edges["extrato"]


def test_veredito_suspeitos_runs_after_the_suspects_and_the_statement_and_before_the_warnings():
    """veredito_suspeitos reads curated.lancamentos_suspeitos (which must have succeeded) and the statement tables
    (df-z4fh.3), whatever extrato's outcome, and runs before data_warnings."""
    job = _load()
    lvl = _level_index(job)
    assert lvl["lancamentos_suspeitos"] < lvl["extrato"] < lvl["veredito_suspeitos"] < lvl["data_warnings"]
    task = _by_name(job)["veredito_suspeitos"]
    assert task.params == {"source": "raw", "target": "curated"}
    assert {e.task: e.when for e in task.depends_on} == {
        "lancamentos_suspeitos": ["succeeded"], "extrato": ["succeeded", "failed"]}
    edges = {e.task: e.when for e in _by_name(job)["data_warnings"].depends_on}
    assert edges["veredito_suspeitos"] == edges["extrato"]


def test_conferencia_fatura_runs_after_the_statements_and_the_last_curated_writer_and_before_the_warnings():
    """conferencia_fatura reads curated.fact_card_statement (which must have succeeded), cleansed.bills and
    cleansed.transactions (df-acz4); it is serialized after the last curated writer (veredito_suspeitos, whatever
    its outcome), so it is now the last one, and runs before data_warnings."""
    job = _load()
    lvl = _level_index(job)
    for upstream in ("transform", "transform_bills", "fact_card_statement", "veredito_suspeitos"):
        assert lvl[upstream] < lvl["conferencia_fatura"], f"conferencia_fatura must run after {upstream}"
    assert lvl["conferencia_fatura"] < lvl["data_warnings"]
    task = _by_name(job)["conferencia_fatura"]
    assert task.params == {"source": "cleansed", "target": "curated"}
    assert {e.task: e.when for e in task.depends_on} == {
        "fact_card_statement": ["succeeded"], "veredito_suspeitos": ["succeeded", "failed", "skipped"]}
    edges = {e.task: e.when for e in _by_name(job)["data_warnings"].depends_on}
    assert edges["conferencia_fatura"] == edges["extrato"]
