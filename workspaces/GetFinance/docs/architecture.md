---
topic: Architecture — Data Flow & Layer Schema
read_when: When you need the end-to-end pipeline flow, the schema of each storage layer, or the Excel report structure
last_synced_at: "2026-06-03T00:00:00Z"
related_issues: []
---

# Architecture — Data Flow & Layer Schema

## Pipeline Flow

```
Pluggy API
   │  landing/landing2raw.py    (fetch: accounts + transactions + investments, all VARCHAR)
   ▼
data/raw/<entity>/ingestion_date=*/data.parquet   (partitioned Parquet, raw JSON flattened to strings)
   │  raw2cleansed/raw2cleansed{,_accounts,_investments}.py
   │      (transform: dedupe by id, type-cast, rename accountName→account)
   ▼
data/cleansed/<table>/   (Delta tables: transactions, accounts, investments,
                          accounts_unmapped, investments_unmapped)
   │  cleansed2curated/*.py     (build star schema: conformed dims + accumulating facts)
   ▼
data/curated/<table>/    (Delta tables: dim_account, dim_investment, dim_calendar,
                          fact_transaction, fact_balance_snapshot, fact_investment_position)
   │
   ▼
scripts/report/export_bridge.py ──► report/bridge/*.csv ──► Power Query ──► develop.xlsx
```

Compute is **Polars + delta-rs** (`deltalake`). No DuckDB, no `.db` files.

`raw` is a partitioned Parquet directory (`data/raw/`); `cleansed` and `curated` are Delta table
directories under `data/cleansed/` and `data/curated/` respectively. All layer paths are defined
in `connections/connections.yaml`. Accumulating tables (`cleansed.transactions` by `id`,
`fact_balance_snapshot` by `account_uuid+snapshot_date`, `fact_investment_position` by
`holding_uuid+snapshot_date`) use Delta MERGE (upsert); all other tables are full overwrites.

## Layer Schemas

### raw — partitioned Parquet
Files written to `data/raw/<entity>/ingestion_date=<YYYY-MM-DD>/data.parquet` by
`landing/landing2raw.py`. Every Pluggy JSON key becomes a `VARCHAR` column (union of keys across
all records). `transactions` carries an injected `accountId`, `accountName`, and `ingested_at`.
`investments` carries an injected `itemId` and `ingested_at`. No types, no dedup. The
`investments` partition is empty when the connected Pluggy items don't expose investment data
(clean seam per the data-model redesign spec §8).

How the fetch fails and writes (df-ahua.10):
- Every Pluggy call comes before every write. A call that does not answer 200 raises
  (`Pluggy GET /accounts answered HTTP 500`: route and status only, never the key or the body), so the
  `fetch` task fails, its retry reruns it, and `job_failed` fires. A failed run writes nothing.
- Pagination is bounded: page-number endpoints (bills, investments) stop when `total` rows are in,
  and also on an empty page (a `WARNING` line, the rows read are kept); cursor pagination
  (`/v2/transactions`) raises when a `next` repeats; both raise after `PLUGGY_MAX_PAGES` pages.
  A full-URL `next` is followed only if it is `https://api.pluggy.ai`, else it raises unsent.
- A partition is written to `.data.parquet.tmp` in its directory, flushed, then moved onto
  `data.parquet` with `os.replace`: a crash leaves the old file or the new one, never a torn one. The
  temp name matches no reader glob and the backup skips it; the next write of that partition
  overwrites a leftover. A same-day rerun still replaces the day's file (df-fs58). On Windows the
  replace fails while another process holds `data.parquet` open; the task then fails.

### cleansed — Delta tables
Delta tables at `data/cleansed/<table>/`: `transactions`, `accounts`, `investments`,
`accounts_unmapped`, `investments_unmapped`.

`transactions` key columns:
```
id VARCHAR, date DATE, amount DOUBLE, category VARCHAR,
description VARCHAR, account VARCHAR, ingested_at
```
- Deduplicated: MERGE on `id`, keeping the latest `ingested_at`.
- `amount` is in the account's currency (BRL), the value curated sums (DataForge df-ahua.3.1). For a
  purchase in another currency Pluggy sends the foreign value in `amount`, its code in `currencyCode`
  and the BRL value the bank converted in `amountInAccountCurrency` (null on every BRL row): cleansed
  takes `amountInAccountCurrency` when it is set, else `amount`. The foreign value and its currency
  stay in raw only: an extra column would not reach the existing table, since the MERGE drops source
  columns the target lacks, so carrying them means a schema change plus a rebuild. A row cleansed
  wrote earlier with the foreign value is corrected by the next run's MERGE (the id is still in raw).
- `accountName` (raw) → `account` (cleansed).
- `category` carries the raw Pluggy value untranslated (no in-ETL translation — LOCKED decision
  A10); the workbook's `Mapeamento` layer owns category overrides.

### curated — Delta tables (star schema)
Delta tables at `data/curated/<table>/`, one table per script under `scripts/cleansed2curated/`:

- `dim_account` — one row per stable account identity (`account_uuid`). Full overwrite.
- `dim_investment` — one row per stable investment identity. Full overwrite.
- `dim_calendar` — date dimension (see below). Full overwrite.
- `fact_transaction` — full overwrite each run from the durable raw source.
- `fact_balance_snapshot` — accumulating: MERGE on `(account_uuid, snapshot_date)`,
  keeps all prior days (history lives only here — A11).
- `fact_investment_position` — accumulating: MERGE on `(holding_uuid, snapshot_date)`,
  same contract as `fact_balance_snapshot`.

Per-table column schemas are specified in the data-model redesign spec
(`docs/superpowers/specs/2026-05-26-finance-data-model-redesign-design.md`).

### curated — `dim_calendar` (built by `dim_calendar.py`)
```
date_id INTEGER PK (YYYYMMDD), date VARCHAR, year, quarter, month,
month_name (PT), day, day_of_week, day_name (PT), week_of_year, is_weekend
```
Default range 2024-01-01 … 2027-12-31, overridable via `--start` / `--end`.

## Report

Since 2026-11-09 (DataForge df-cns) the report logic runs in the ETL: `scripts/report/classify.py`
writes `curated.fact_transaction_classified` and `scripts/report/report_curated.py` writes
`curated.report_*`, both at the end of `finance_daily` and alone as the job `reclassificar`. The
Finanças app (`apps/financas`) reads those tables. The Excel workbooks under `report/` are ignored
(history only; `docs/workbook.md` describes the old Power Pivot design).

## Data checks (ADR 0004, DataForge df-ahua.3 and df-ahua.22)

Every run checks the data before the write it protects, with one function, `validate(df, table,
rules, key)` in `functions.py`, and rule lists kept as data next to it (`RAW_*_RULES`,
`CLEANSED_TRANSACTIONS_RULES`, `FACT_*_RULES`, and the reconciliation lists used with
`layer_totals`). Each rule is `(name, severity, check)`:

- `impossible`: `validate` raises `DataValidationError`, the task fails before writing, the rest of
  the job is skipped and curated keeps the last good day. The error lists every broken rule with a
  count and a few ids (never amounts or names: logs may be shared).
- `suspicious`: recorded in `data/validation/<check>.json` (connection `validation`) and the write
  goes on. The `data_warnings` task at the end of `finance_daily` prints them and fails, which is
  what the `alert_warnings` message task listens to.

Where they run: the four `raw2cleansed` scripts check the combined raw before `latest_by_key` (the
live check of Pluggy's payload, too), the transactions frame before the merge, and after the write
that every id of the latest raw is in cleansed and no id cleansed had is gone. The curated scripts
check keys, nulls and sign conventions before each write, and `cleansed2curated.py` checks that each
account's rows and amount total are carried from cleansed to `fact_transaction`.

What the raw rules also check (DataForge df-ahua.4.1 to 4.7): `read_raw` gives every row the day of
its partition path (`ingestion_date`), and `ingested_at` must be that day and not after the run day
(UTC, one day of slack), since a later `ingested_at` wins `latest_by_key` over every fetch to come
(impossible). Suspicious: the same id twice in one fetch with different content (an identical copy
is harmless; raw is never rewritten and the next fetch wins, so it warns instead of blocking every
run), a null `type`/`status`, a field null on every row of one fetch, broken
encoding (UTF-8 read as cp1252, df-ahua.4.4), a currency other than BRL on an account, a bill
or a holding (summed as BRL, with no converted value to take), a transaction in another currency
without `amountInAccountCurrency` (its foreign value would be summed as BRL; df-ahua.3.1), a date
before 2000 or more than a year after its fetch, and a missing column of bills or investments (Pluggy
omits fields per fetch there). An `amountInAccountCurrency` that is set and is not a number is
impossible, like an `amount` that is not one.

What the curated rules also check (df-ahua.4.8 to 4.11): every fact's `account_uuid` has a
`dim_account` row, and `dim_account` has every cleansed account (D3), so an inner join that would drop
an account's rows stops the run instead (impossible). Each snapshot fact is reconciled with its source:
`fact_card_statement` with `cleansed.bills` (minus the re-issued twins it drops), `fact_fatura_mes`
with `fact_card_statement`, `fact_balance_snapshot` with `raw.accounts`, `fact_investment_position`
with the mapped holdings. Each of them checks its columns, that amounts and dates are typed
(impossible), and the values readers use: a null amount, a negative `current_value` or a due date
more than a year away is suspicious; a `snapshot_date` after the run day is impossible.

Job wiring: the engine marks a run failed only when a leaf or an `error` task fails, so
`finance_daily` and `reclassificar` end in a `job_failed` error task. `data_warnings` runs once
(`retry: {attempts: 1}` on the task): it answers from the findings this run recorded, so a retry only
repeats it. The job-level `retry:` of `finance_daily` does not reach its tasks (DataForge df-a16k):
a task without its own gets the engine default, 3 attempts 30 s apart. The message tasks
`alert_failure` and `alert_warnings` ship inactive until a channel is chosen (DataForge df-ahua.3.2).

Data mutation (DataForge df-ahua.4) tests the checks themselves: `tests/data_mutator.py` corrupts a
small valid dataset per boundary in named ways (null, duplicate key, flipped sign, broken encoding,
shifted date, empty input, missing column, a field gone from today's fetch, a repeated or cut
partition...), and `tests/test_data_mutation.py` records, for every pair, the rule that fires, why
it is tolerated, or the bead of the hole (a strict xfail). The 90 holes it first found are closed
(df-ahua.4.1 to 4.11); a new one lands as a strict xfail naming its bead.
`tests/test_data_mutation_e2e.py` runs the same corruptions through `raw2cleansed.py` as a subprocess.

## Conventions & Gotchas

- `scripts/functions/functions.py` is loaded via `exec()` at the top of each script, not imported
  as a module — keep it import-side-effect free.
- `landing2raw.py` writes every Pluggy field as `VARCHAR`; type-casting happens at the cleansed
  layer. Don't assume typed columns before `transform`.
- Cross-platform paths: WSL `/mnt/c/...` paths are converted to Windows `C:\...` via `_to_win()`
  when shelling out to PowerShell. Keep this in mind for any new Windows-facing subprocess calls.
- All generated artifacts live under `data/` (gitignored). Each table is its own Delta directory
  (`data/cleansed/<table>/` or `data/curated/<table>/`); there is no shared lock to contend with.
- The orchestrator execution DB (SQLite) is at `<workspace>/data/dataforge.db`.

## Planned Evolution

GetFinance is intended to become a multi-user, configurable finance web app:

```
Pluggy API → ETL (user-configured) → Delta Lake (per-user) → FastAPI → Frontend
```

Phases:
1. **Export API** — FastAPI reads the curated layer, exports CSV/JSON/Excel.
2. **Frontend** — visualization once the export API is stable.
3. **Multi-user** — auth, per-user Pluggy credentials, per-user data paths.
4. **Configurable pipeline** — users choose accounts, date ranges, layers, and custom categories.

Open stack decisions: frontend (Next.js vs React), auth/user store (Supabase vs Postgres).
