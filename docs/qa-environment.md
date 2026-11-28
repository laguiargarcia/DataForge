# QA Environment — DataForge-QA

`DataForge-QA/` is a **separate QA/validation environment**, a sibling of this dev repo. It
exists to validate the GetFinance report workbook against independent bank data. This
doc is a durable pointer so sessions don't lose track of it.

> **Workbook era.** Since 2026-11-09 (df-cns) the Excel workbook is not maintained, and this repo
> no longer has a synthetic Show workspace: the public workspace is `workspaces/GetFinance`, whose
> demo job is `finance_demo` (df-bsb2.2). The QA copy is a frozen setup from 2026-07-01 with one
> workspace of its own under `DataForge-QA/workspaces/` (still under the old Show name; written
> `<qa>` below) and its own old copy of the code. Whether to keep this doc is df-toxf.

## Location

- Windows: `C:\Users\<user>\Desktop\projects\DataForge-QA\`
- WSL:     `/mnt/c/Users/<user>/Desktop/projects/DataForge-QA/`

Sibling directories under `.../projects/`: `DataForge/` (this dev repo), `DataForge-QA/`
(this doc), `Old/` (ignore).

## Git status

**Not a git repo.** `git -C .../DataForge-QA rev-parse` → "not a git repository". It is an
unzipped setup copy (has `.gitignore`/`.gitleaks.toml`/`.github/` as files but **no `.git/`**).
Do not run git operations in it.

## Purpose & KEEP-CLEAN rule

- Runs the **production** report workbook (`Relatorio_Financeiro.xlsx`) against a **second
  Pluggy item** — the test bank item, separate from the real prod bank — so the report
  can be validated with independent, real bank data. (Despite the workspace's old Show name,
  its `fetch` task is the real `landing/landing2raw.py` + live Pluggy creds in `.env`, **not**
  the synthetic generator.)
- **Keep it CLEAN.** Only the pipeline + workbook + its own `README.md` / `SETUP-QA.md` live
  there. Do **not** write analysis / findings / scratch files into `DataForge-QA/`. All QA
  findings docs belong in **this** dev repo (`DataForge/docs/`).
- The `.env` under `DataForge-QA/workspaces/<qa>/` holds **live Pluggy credentials** — treat
  the folder/zip as a secret.

## Relationship to the DEV repo

| | DEV (`DataForge/`) | QA (`DataForge-QA/`) |
| --- | --- | --- |
| Role | development | validation |
| Git | tracked (`main`) | untracked (unzipped copy) |
| Workbook | `workspaces/GetFinance-Dev/report/resumo.xlsx` (v2, model-free) + `develop.xlsx` (both gone since df-sgyf) | `workspaces/<qa>/report/Relatorio_Financeiro.xlsx` (the shipped prod workbook) |
| Data source | Dev curated / synthetic demo | the real test bank's Pluggy item |
| Findings docs | live HERE (`docs/`) | none — kept clean |

At setup (2026-07-01) the QA `workspaces/<qa>/scripts/` were **byte-identical** to the dev
repo's synthetic Show workspace of that time (verified `diff -rq --exclude=__pycache__`, exit 0).
Nothing has updated them since: the QA copy runs the ETL of July 2026, not the current
`workspaces/GetFinance/scripts/`.

## Workbook & bridge CSVs

- **Workbook:** `workspaces/<qa>/report/Relatorio_Financeiro.xlsx` (binary `.xlsx`,
  the "Resumo" dashboard is the QA surface under review). This is the only `.xlsx` in the QA
  repo; the DEV repo versions **no** `report/` dir — QA carries the shipped workbook.
- **Bridge CSVs it reads:** `workspaces/<qa>/data/bridge/` — the workbook's Power
  Query resolves them via the `BridgeFolder` cell (`_aux!M1`), pre-pointed at
  `C:\Users\<user>\Desktop\projects\DataForge-QA\workspaces\<qa>\data\bridge\`
  (trailing backslash required; repoint if the folder moves). Files present:
  - `dim_account.csv`, `dim_calendar.csv`, `dim_counterparty.csv`, `dim_investment.csv`
  - `fact_balance_snapshot.csv`, `fact_fatura_mes.csv`, `fact_investment_position.csv`,
    `fact_transaction.csv`
  - `report_liquidity.csv`
- The bridge CSVs are (re)generated as job tasks — `scripts/report/export_bridge.py` and
  `scripts/report/build_report.py` — when `dataforge run <qa> finance_daily` runs in the QA copy.
  Re-run the job, then **Data → Refresh All** in Excel to update the report.

## How to inspect the workbook

`Relatorio_Financeiro.xlsx` is a **binary** file — grepping it yields nothing useful. To read
sheet values / measures, use the **excel-com MCP** tooling (sbroenne `mcp-excel.exe`,
registered in this repo's `.mcp.json`). Excel must have the file **closed** first (excel-com
opens it exclusively). Do not save/modify the QA workbook during inspection — keep the QA repo
pristine.

> 2026-11-21 (df-wfiu): `export_bridge.py` and `build_report.py` were removed from the GetFinance workspace; the bridge CSVs are no longer produced. History only.
