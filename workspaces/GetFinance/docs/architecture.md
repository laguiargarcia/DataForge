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
                          fact_transaction, fact_balance_snapshot, fact_investment_position,
                          conferencia_saldo, lancamentos_suspeitos, extrato, extrato_saldos,
                          extrato_arquivos, veredito_suspeitos, extrato_sem_lancamento,
                          acerto_por_motivo, conferencia_fatura)
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
- Gone from the latest fetch (DataForge df-75ol, rule of df-7twv): Pluggy returns an account's whole
  window on every fetch, so the latest fetch (the raw partition with the greatest `ingestion_date`)
  covers, for each account in it, the days from that account's earliest date in it on. An id of that
  account dated inside the window and absent from that fetch is gone. It leaves cleansed (raw keeps it)
  on a CREDIT account, and on a BANK account only when its latest known `status` is PENDING (a pending
  row that vanishes is a duplicate); a POSTED bank row that vanishes stays, because the account balance
  and the bank statement show the money moved. An id dated before the window and an account absent from
  the fetch stay. When a run would newly remove more than 20 ids and more than 10 % of an account's ids
  in the window, the fetch looks incomplete: nothing leaves that account and the check
  `cleansed.transactions vs latest fetch` records a suspicious finding. A removal applied on an earlier
  run does not count again (with an empty cleansed, as after `reprocess.py`, every removal is new).
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
- `fact_transaction` — full overwrite each run from the durable raw source. The two legs of a bill
  payment get `etl_tag` `card_payment_bank` / `card_payment_card` (rules in `cleansed2curated.py`): by
  category (`05100000` on both legs; `05060000` on the card, the automatic debit since 07/2026), or by
  mirror, when one leg has its tag, the other one is the same |amount| within `MIRROR_WINDOW_DAYS` on the
  other account (the bank leg of the automatic debit comes as Investments, no category or `05060000`).
  A Pix `05100000` debit on the bank is a payment only by mirror: Pluggy also files a Pix QR Code paid to
  a merchant under that category (df-t6h2). Its mirror is any inflow on a card of the same |amount|
  within the window, even one Pluggy mislabeled, which then gets its tag too (df-zpme). A leg with no leg of the other side (`bill_payment_leg_without_pair_rule`
  in `functions.py`, suspicious, df-7lp0) warns once it is older than `MIRROR_WINDOW_DAYS`: it is what a
  new category of one side, as in 07/2026 (df-sf61), looks like.
- `fact_balance_snapshot` — accumulating: MERGE on `(account_uuid, snapshot_date)`,
  keeps all prior days (history lives only here — A11).
- `fact_investment_position` — accumulating: MERGE on `(holding_uuid, snapshot_date)`,
  same contract as `fact_balance_snapshot`.
- `conferencia_saldo` — the balance check (df-z4fh.1), full overwrite. See below.
- `lancamentos_suspeitos` — the suspect transactions (df-z4fh.2), full overwrite. See below.
- `extrato`, `extrato_saldos`, `extrato_arquivos` — the bank statement PDFs (df-z4fh.5), full overwrite.
  See below.
- `veredito_suspeitos`, `extrato_sem_lancamento`, `acerto_por_motivo` — the statement's verdict on each suspect
  (df-z4fh.3), full overwrite. See below.
- `conferencia_fatura` — the card check by each bill's total (df-acz4), full overwrite. See below.

Per-table column schemas are specified in the data-model redesign spec
(`docs/superpowers/specs/2026-05-26-finance-data-model-redesign-design.md`).

### curated — `conferencia_saldo` (built by `cleansed2curated_conferencia_saldo.py`)
The balance of a BANK account in `raw.accounts` is the total (account plus automatic investment), so
between two consecutive snapshots (distinct `updatedAt`) its change must equal the sum of the account's
transactions with `de < instant <= ate`. One row per (BANK account, consecutive snapshot pair):
```
account_uuid, de, ate (updatedAt, UTC), coletado_em (day the `ate` snapshot first arrived),
saldo_de, saldo_ate, variacao_saldo, soma_lancamentos, n_lancamentos,
diferenca (= variacao_saldo - soma_lancamentos), diferenca_acumulada, estado
```
- The transactions are `cleansed.transactions` (the set the report sums). Cleansed keeps only the day of
  each, so the instant comes from `raw.transactions` (the full `date` of the newest fetch of the id); an
  id without one falls back to the day at midnight UTC. Without the time, a transaction late in the day
  lands on the wrong side of that day's sync.
- `estado`: `fecha` when `|diferenca| <= TOLERANCIA` (1.00, `scripts/report/conferencia_saldo.py`);
  `fecha_com_vizinho` when it does not close alone but does with one adjacent interval that does not
  close alone either (the bank sometimes moves the balance one sync before the transaction's date; each
  interval is in one pair at most); `em_aberto` for an account's newest interval when it closes neither
  alone nor with the previous one (its next neighbour has not come yet); `nao_fecha` otherwise. `diferenca` is always the raw one.
- It only measures: nothing here changes a transaction (df-z4fh).
- The one suspicious finding (`new_balance_break_rule` in `functions.py`): the third-newest interval of
  an account is `nao_fecha` and the account's newest snapshot arrived on the run day. Two snapshots
  later, a transaction the bank posts after a weekend or one sync late has arrived, so a healthy account
  stays quiet; a run without a new snapshot of the account repeats nothing. A new break therefore warns
  two fetches late, once.
- Two cases where a break gives no warning (the table still shows it, the warning does not): the run in
  which the break becomes the third-newest interval fails between the fetch and this task (the next run
  has moved it to fourth); and an older interval that changes later (a transaction arrives or vanishes
  long after its date). The task prints the count per `estado` and the non-closing intervals among the 3
  newest of each account, with dates and state only: the amounts are in the table.

### curated — `lancamentos_suspeitos` (built by `cleansed2curated_lancamentos_suspeitos.py`)
Each transaction of `cleansed.transactions` that a reason marks, with the balance-check interval it falls
in, so a break in `conferencia_saldo` can be read next to its suspects. One row per (transaction, reason):
```
transaction_id, account_uuid, date, amount, description, status, motivo,
primeira_coleta, ultima_coleta, n_coletas (the fetches, days of raw.transactions, the id came in),
intervalo_de, intervalo_ate, intervalo_estado (the conferencia_saldo interval with de < instant <= ate)
```
- The reasons are named expressions in `MOTIVOS` (`scripts/report/lancamentos_suspeitos.py`); a new
  reason is one more entry. A transaction may have several. They come from what DataForge df-7twv measured
  against the balance and the bank statement:
  - `sumiu_da_coleta`: the id is not in the newest fetch of its account, although its day is inside the
    window that fetch returns (its first to last transaction day; compared by day, cleansed keeps the day);
  - `pendente_que_sumiu`: the previous one, with status `PENDING` (usually a duplicate of the posted one);
  - `aplicacao_automatica`: the description is an automatic investment or redemption line of the account
    (`PADRAO_APLICACAO_AUTOMATICA`): internal movement that does not change the total balance;
  - `uma_coleta_so`: the id came in one fetch only, and not in the newest one.
- The instant that picks the interval is the one `conferencia_saldo` uses (`instantes` in
  `conferencia_saldo.py`). A transaction outside every interval (a card, before the first snapshot,
  after the last) has no interval.
- It only marks: being a suspect changes nothing in the data or the report, and the table has no
  suspicious rule, so it never warns. The task prints the count per reason, never an id or an amount.

### curated — `extrato`, `extrato_saldos` and `extrato_arquivos` (built by `cleansed2curated_extrato.py`)
The bank only exports the checking-account statement as a PDF. The owner drops each one in
`data/extratos/` (connection `extratos`; `data/` is not versioned, and the `backup` job zips the folder with
raw, since a statement has no rebuild). The task `extrato` reads every `.pdf` there with `pypdf` and writes:
```
extrato:        arquivo, account_uuid, dia, ordem (position within the day in the file), descricao,
                valor (signed: a debit is negative), saldo_do_dia (null when the file has no balance that day)
extrato_saldos: account_uuid, dia, arquivo, saldo_do_dia, saldo_anterior, soma_lancamentos, n_lancamentos,
                diferenca (= saldo_anterior + soma_lancamentos - saldo_do_dia), fecha
extrato_arquivos: arquivo, account_uuid, emitido_em, n_lancamentos, n_saldos, linhas_ignoradas (lines of the
                list that are neither an entry nor a balance: printed and kept, never a finding)
```
- The layout read is described in `scripts/report/extrato.py` (pure functions over the text of the pages):
  the list after the line `Lançamentos` and before `Posição consolidada`, newest first; an entry is a date
  and a description with its value at the end of the line or on a later one (also across a page break);
  a line with only a date and a value is that day's closing balance. Future entries (`Lançamentos futuros`)
  are left out. A file without the closing line is cut short and is not read.
- The account is the BANK account of `cleansed.accounts` whose `transfer_number` (agency and account) or
  `number` matches the agency and account in the PDF header, matched at run time: no account number lives
  in the code.
- A bad file (unreadable or encrypted, another layout, an impossible date, an account that matches no BANK
  account or more than one) is left out; the good ones are written, then the task fails naming every bad
  file and why. The task runs once (`retry: attempts: 1`): a retry would read the same files.
- Files may overlap. Each (account, day) takes its entries from one file: the one with more entries that
  day, then the newest by its "Emitido em", then the name. So a day is never counted twice and two equal
  entries of one day stay two, and a file that only has that day's balance (its opening balance) does not
  hide the entries another file has. The day's balance comes from any file that has it; two files with
  different balances for one day fail the task before any write, naming the day and both files.
- `extrato_saldos` checks each balance against the previous one: an entry counts toward the first balance
  on or after its day (the bank prints no balance on weekends and on some days), and `fecha` is
  `|diferenca| <= 0.005`. The first balance of an account has no previous one (`fecha` null). The task
  prints the count of days that do not close and their dates, never an amount; it has no suspicious rule,
  since an old statement would repeat the same finding every day.
- An empty or missing folder writes empty tables. With PDFs in the folder and `pypdf` not installed, the
  task prints a warning, writes empty tables and succeeds. It runs after `lancamentos_suspeitos` only to
  keep one curated writer at a time (`when: [completed, skipped]`), so a failed balance check does not stop
  it. It only reads and records: nothing here changes a
  transaction, a fact or a report number (df-z4fh).

### curated — `veredito_suspeitos`, `extrato_sem_lancamento` and `acerto_por_motivo` (built by `cleansed2curated_veredito_suspeitos.py`)
With the statement read, the task `veredito_suspeitos` matches every transaction of `cleansed.transactions`
with a statement line, and only then reads the verdict of each suspect of `lancamentos_suspeitos`: matching
only the suspects would let a suspect take the line of a normal transaction of the same amount.
```
veredito_suspeitos:     transaction_id, account_uuid, date, amount, motivo, veredito, dia_banco (the day of the
                        transaction in the bank's time zone), extrato_dia, extrato_ordem (its statement line)
extrato_sem_lancamento: arquivo, account_uuid, dia, ordem, descricao, valor (statement lines no transaction matched)
acerto_por_motivo:      motivo, n, n_no_extrato, n_fora_do_extrato (one row per reason of MOTIVOS, zeros included)
```
- The rule is in `scripts/report/veredito_extrato.py`. A pair is the same account and the same amount to the
  centavo, and days at most `TOLERANCIA_DIAS` (4) apart either way. The transaction's day is its instant from
  raw (`instantes` in `conferencia_saldo.py`, UTC) in `America/Sao_Paulo`: in UTC, late-evening entries fall on
  the next day. One to one and greedy: the closest pair first, then by the transaction's day, POSTED before
  PENDING (the statement lists what was posted; an equal pending one is the duplicate), its id, and the
  line's day and position. In a run of equal amounts on nearby days (centavo yields) another assignment could
  pair one more; on the real statement that is 2 of 1129 pairs.
- The tolerance was measured on the 2026 statement (DataForge df-z4fh.3): with 3 days three real debit purchases
  the bank posted 4 days later have no pair; with 5 the only extra pair is a centavo yield.
- The period a statement covers is, per account, the union of each file's span: from the day after its first
  balance (the opening balance closes a day the file does not list), or its first entry if earlier, to its last
  day with an entry or a balance. A gap between files is not covered.
- `veredito`: `no_extrato` when the transaction has a pair; `fora_do_extrato` when it has none and every day
  its pair could be on (its day plus and minus the tolerance) lies in one covered period; otherwise
  `sem_extrato_no_periodo` (an account without a statement, such as the card, a day outside the period or near
  its edge, or no statement read yet). `acerto_por_motivo` counts them per reason: the `sem_extrato_no_periodo`
  ones are `n - n_no_extrato - n_fora_do_extrato`.
- Without statement tables (no PDF read yet) every suspect is `sem_extrato_no_periodo` and the task succeeds.
  It runs after `lancamentos_suspeitos` succeeded (otherwise that table is yesterday's) and after `extrato`
  whatever its outcome (a failed `extrato` wrote the good files or nothing). It has no suspicious rule, since an
  old statement would repeat the same finding every day; the task prints counts per verdict and per reason, the
  lines without a transaction and the transactions of the covered period without a line, never an id or an
  amount. It only reads and records (df-z4fh).

### curated — `conferencia_fatura` (built by `cleansed2curated_conferencia_fatura.py`)
The card has no balance per fetch the way a BANK account has (`conferencia_saldo` covers BANK only); it has
the bill. One row per closed statement of `fact_card_statement` (the total Pluggy returns in `/bills`, without
the re-issued twins), with two checks against that total:
```
account_uuid, statement_id, due_date, total_fatura,
saldo_anterior, soma_lancamentos, n_lancamentos,
diferenca_lancamentos (= total_fatura - saldo_anterior - soma_lancamentos), estado_lancamentos,
pago, n_pagamentos, diferenca_pagamento (= total_fatura - pago), estado_pagamento
```
- The rule is in `scripts/report/conferencia_fatura.py`. A bill payment is a card transaction with
  `amount < 0` and a `categoryId` of `CATEGORIAS_PAGAMENTO`: `05100000` up to 06/2026, and `05060000` for the
  automatic debit since 07/2026 (measured, df-acz4). Everything else on the card (purchases, fees, refunds,
  balance-adjustment credits) is a transaction of the bill.
- Transactions: the card transactions of `cleansed.transactions` whose `cc_bill_id` is the bill (or a
  re-issued twin of the same account and due month), payments left out. `saldo_anterior` is what the
  previous bill left unpaid (its total minus its `pago`), never negative: an extra payment shows only in the
  payment check. A negative `diferenca_lancamentos` is a transaction too many (the purchase Pluggy returned
  under two ids, DataForge df-mk25), a positive one a transaction missing.
- Payment: each payment counts for the bill of the same card whose due date is nearest to it, within
  `JANELA_PAGAMENTO_DIAS` (10) days; outside every window it counts for none. Its `cc_bill_id` is not used:
  up to 03/2026 it was the paid bill's, since then the next bill's. A negative `diferenca_pagamento` is a
  payment too many (the payment copy Pluggy stopped returning, DataForge df-75ol), a positive one a bill not
  (fully) paid.
- `estado_lancamentos` and `estado_pagamento`: `fecha` when `|diferenca| <= TOLERANCIA` (0.01: on the real data
  every bill that closes does so to the centavo); `historico_incompleto` when it does not close and the card's
  first transaction is later than the start of what the check looks at (the cycle, `DIAS_DO_CICLO` = 40 days
  before the due date, or the payment window); `a_vencer` (payment only) when it does not close and the
  payment window has not ended on the run day; `sem_total` when the bill has no total; `nao_fecha` otherwise.
- It reconciles one row per `fact_card_statement` statement before the write. It only measures: nothing here
  changes a transaction (df-z4fh), and it has no suspicious rule, so a bill that does not close never warns
  (owner's call; a warning for a new bill is DataForge df-5g8b). The task prints the count per state of each
  check, never an amount. The source is Pluggy's own total; the bank's bill PDF as an independent one is
  DataForge df-lxdb.

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
Finanças app (`apps/financas`) reads those tables, and its backend (`apps/financas/backend`) edits the
categories, rules and per-transaction corrections in `data/edicao.db` and asks DataForge for a
`reclassificar` run when the user clicks Aplicar (DataForge df-cns.8). The Excel workbooks under `report/` are ignored
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
that every id of the latest raw is in cleansed and no id cleansed had is gone, but for the ids removed
as gone from the latest fetch, which must be gone. The curated scripts
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
