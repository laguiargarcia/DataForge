---
topic: Commands
read_when: When running the pipeline, individual scripts, the scheduler, or debugging a run
last_synced_at: "2026-11-17T00:00:00Z"
related_issues: []
---

# Commands

GetFinance is a DataForge workspace: the `dataforge` CLI comes from a DataForge venv, with
`DATAFORGE_HOME` set to the DataForge folder whose `workspaces/GetFinance` is this checkout. Where
development and prod live is in the README ("Where it lives"). A change is made in a DataForge
worktree like any other; how to run the tests and commit with `GETFINANCE_DATA`: DataForge's
`docs/commands.md`, section "GetFinance workspace".

## Configuration

`.env` in the workspace root (gitignored; copy `.env.example`), loaded via `job.yaml`
`env_file: .env`:

```
CLIENT_ID=<pluggy client id>
CLIENT_SECRET=<pluggy client secret>
ITEM_IDS=<comma-separated pluggy item IDs>
```

`config/investments.yaml` (gitignored; copy `config/investments.example.yaml`) maps each Pluggy
holding to a stable slug and a display alias.

`config/merchant_normalization.yaml` holds the rules that turn a transaction description into its
merchant key: the first rule whose `match` hits the description has its `strip` patterns removed.
`MERCHANT_NORM_CONFIG` points at another rules file.

Layer paths live in `connections/connections.yaml`, resolved relative to the workspace root.
All generated outputs (raw Parquet, cleansed/curated Delta tables, `edicao.db`, `dataforge.db`) live
under `data/` (gitignored).

## Run a job

```bash
dataforge run GetFinance finance_daily     # the daily job: Pluggy fetch, all layers, report tables
dataforge run GetFinance finance_demo      # the same pipeline on synthetic data, no .env (see the README, Demo)
dataforge run GetFinance reclassificar     # reapply the category rules to the existing curated (no Pluggy)
dataforge run GetFinance backup            # zip data/raw + data/edicao.db (see the README, Backup)
```

`bin/run.sh` (WSL/Linux) and `bin/run.ps1` (PowerShell) activate the venv of the DataForge folder
three levels up and run `finance_daily`.

## Production schedule (Windows Task Scheduler)

In prod the jobs run from Windows scheduled tasks, NOT cron. Each trigger in `triggers/` becomes one
task, `\DataForge\GetFinance\trigger\<trigger>`, whose action runs the prod venv's
`dataforge.exe scheduled-run GetFinance <trigger>` with `DATAFORGE_HOME` set to the DataForge folder.
`scheduled-run GetFinance daily` runs the `daily` trigger, that is the `finance_daily` job.

The triggers are prod's own files (`triggers/` is gitignored and the deploy never touches it). They are
made from the app or the API, or by hand; prod has these two (cron in UTC):

```yaml
# triggers/daily.yaml: 06:00 in Brasília
name: daily
cron: 0 9 * * *
enabled: true
jobs:
- job: finance_daily
  params: {}
  workspace: GetFinance
```

```yaml
# triggers/backup.yaml: Sunday 10:00 in Brasília
name: backup
cron: 0 13 * * 0
enabled: true
jobs:
- job: backup
  params: {}
  workspace: GetFinance
```

```powershell
# Inspect / run the task (Windows)
schtasks /query /tn "\DataForge\GetFinance\trigger\daily" /v /fo LIST
schtasks /run   /tn "\DataForge\GetFinance\trigger\daily"   # run now
```

## Inspect / validate

```bash
dataforge validate -w GetFinance          # check workspace + job config
dataforge jobs list -w GetFinance
dataforge execucoes list -w GetFinance    # past runs
dataforge execucoes logs <id> -w GetFinance
```

## Run individual scripts

Each script reads `connections/connections.yaml` to resolve layer paths. Run from the workspace
dir inside an activated venv:

```bash
# Landing (fetch from Pluggy → raw partitioned Parquet)
python scripts/landing/landing2raw.py --target raw

# Raw → Cleansed (Delta tables under data/cleansed/)
python scripts/raw2cleansed/raw2cleansed.py --source raw --target cleansed
python scripts/raw2cleansed/raw2cleansed_accounts.py --source raw --target cleansed
python scripts/raw2cleansed/raw2cleansed_bills.py --source raw --target cleansed
python scripts/raw2cleansed/raw2cleansed_investments.py --source raw --target cleansed

# Cleansed → Curated (Delta tables under data/curated/); the order is the one in jobs/finance_daily/job.yaml
python scripts/cleansed2curated/cleansed2curated_accounts.py --source cleansed --target curated
python scripts/cleansed2curated/fact_balance_snapshot.py --source raw --target curated
python scripts/cleansed2curated/cleansed2curated_investments.py --source cleansed --target curated
python scripts/cleansed2curated/fact_investment_position.py --source cleansed --target curated
python scripts/cleansed2curated/fact_card_statement.py --source cleansed --target curated
python scripts/cleansed2curated/cleansed2curated.py --source cleansed --target curated
python scripts/cleansed2curated/dim_counterparty.py --source cleansed --target curated
python scripts/cleansed2curated/fact_fatura_mes.py --source cleansed --target curated

# Report tables (curated → curated)
python scripts/report/classify.py --source curated --target curated
python scripts/report/report_curated.py --source curated --target curated
```

Rebuild cleansed from raw: `python scripts/reprocess.py --source raw --target cleansed --entity all`.

## Standalone (not in the job DAG)

```bash
# Date dimension (defaults 2024-01-01 .. 2027-12-31)
python scripts/cleansed2curated/dim_calendar.py [--start YYYY-MM-DD --end YYYY-MM-DD]
```

The report is the Finanças app (`apps/financas`), which reads the `curated.report_*` tables. The
Excel workbooks under `report/` are not maintained (README, "Report logic").

## Delta table layout

Each cleansed and curated table is its own Delta directory (e.g. `data/cleansed/transactions/`,
`data/curated/fact_balance_snapshot/`). There are no shared `.db` files and no file-lock
contention — tables can be read independently at any time.
