# GetFinance — the one GetFinance workspace

GetFinance is a DataForge workspace: the daily job (`finance_daily`) collects bank data from Pluggy
and builds the raw, cleansed and curated layers and the report tables behind the Finanças app. Since
2026-11-12 (DataForge df-sgyf) there is **one** GetFinance workspace: this repo. There is no
GetFinance-Dev any more (archived in `projects/_archive/GetFinance-Dev-2026-11-12`) and no deploy
between workspaces.

Why: GetFinance is not in real use yet. The Dev/prod split cost a deploy per change and two copies of
the data, and protected nothing that cannot be rebuilt. A Dev workspace comes back (with
`dataforge deploy`, which stays in the DataForge core) when the Finanças app is in daily use.

## Demo: the whole pipeline on synthetic data, no credentials

`jobs/finance_demo` is `finance_daily` with one task swapped: `fetch` runs
`scripts/synthetic/generate_synthetic_data.py`, which writes six months of fabricated Pluggy
payloads (fixed seed and date, so every run is the same) instead of calling Pluggy. It needs no
`.env`. Two demo inputs are versioned: `config/investments.synthetic.yaml` (the holdings map) and
`config/edicao/*.csv` (the categories and rules, seeded into `data/edicao.db` by the task
`seed_edicao` when it is empty). From a DataForge venv, with `DATAFORGE_HOME` set to the DataForge
folder that holds this workspace:

```bash
DATAFORGE_HOME=<DataForge folder> dataforge run GetFinance finance_demo
```

It writes `data/` here like the daily job, and the Finanças app then shows the synthetic data. To
start over, delete `data/`.

The demo and the real data never share a `data/raw/`:

- The synthetic fetch writes only into an empty `data/raw/` or one it wrote itself (it leaves the
  file `data/raw/SYNTHETIC`), and otherwise exits 1 before writing anything. Every demo task that
  writes waits for `fetch`, so on a checkout with real data the demo writes nothing and ends failed.
- The real fetch (`landing/landing2raw.py`) refuses a `data/raw/` that holds `SYNTHETIC`, before any
  Pluggy call. **To go from the demo to real data, delete `data/` first**: the demo's
  `data/edicao.db` and curated tables are synthetic too.

`tests/test_finance_demo.py` keeps every other task equal to `finance_daily`'s (the one intended
difference is that the demo's `calendar` waits for `fetch`): change a task in both job files.

## The Finanças app

`apps/financas` is a React app over the `curated.report_*` tables. `dataforge serve` publishes its
committed `dist/` at `/apps/GetFinance/financas/` (no Node needed to run it). Its tabs: **Resumo** and
**Conferência** (read only), **Categorias** (the rules and the category dictionary, edited in place) and
**Transações** (search and filter `fact_transaction_classified`, correct one transaction's category,
delete corrections whose transaction is gone). Every save goes straight to `data/edicao.db`; the numbers
change only after **Aplicar**, in the bar every tab shows while there are edits not yet recalculated.

Its backend, `apps/financas/backend/main.py` (`backend:` in `app.yaml`), is what the edit screens call
at `/apps/GetFinance/financas/api/...`: `GET /edicao` (categories, rules, corrections with `encontrada`,
the allowed Tipos, `versao`, `aplicada`, `pendente`), create/change/delete of `/categorias`, `/regras`
(the answer carries the rule's `efeito`: how many transactions it matches, wins and the direction gate
leaves out) and `/correcoes/{id}`, `PUT`/`DELETE /explicacoes/{conta}/{ate}` (marks a balance-check
interval that does not close as an explained divergence, with a reason; the backend takes the interval's
`diferenca` from `curated.conferencia_saldo`, DataForge df-y26v), and `POST /aplicar` (asks DataForge for a `reclassificar` run, queued
behind any job of the workspace) with `GET /aplicar/{id}` for its status. Saving writes straight to
`data/edicao.db`; the validations live in `scripts/report/edicao.py`, the one module that writes there.
Tests: `tests/test_backend_edicao.py`.

Node is needed only to change the front end, from the app folder after `npm ci`:

- `npm test`: the unit tests (vitest).
- `npm run build`: type-check and rebuild `dist/`. Commit `dist/` with every change to `src/`; CI
  (job `app-financas`, needed by the required `tests` check) runs the tests and the build, and the
  PR cannot merge when they fail or the committed `dist/` differs.
- `npm run test:e2e`: builds, then runs Playwright (`npx playwright install chromium` once) against
  a `dataforge serve` of this checkout on port 8765, started with the repo root's
  `.venv/bin/dataforge` (or `DATAFORGE_BIN`). It needs the demo's data (run `finance_demo` first) and
  refuses to start without `data/raw/SYNTHETIC`, so it never shows real data. The values it checks
  and the screenshots in `e2e/referencia/` come from that synthetic data. The `edição` scenarios write
  to the demo's `data/edicao.db` and run `reclassificar` for real; each one undoes what it created
  through the backend and applies again, so the demo data ends as `finance_demo` left it.

## Where it lives

- **Development** happens in the DataForge repo, where this workspace is `workspaces/GetFinance`
  (DataForge df-bsb2.2): a change is made in a DataForge worktree like any other. The repo has no real
  `data/`: the real data stays in prod, read through `GETFINANCE_DATA` (see Tests).
- **Prod** is `workspaces/GetFinance` under the Windows DataForge folder, run daily by the Task
  Scheduler. It holds the real `data/` and `.env`. Nobody edits code there: DataForge's
  `scripts/prod/deploy.sh`, run after each merge to main, installs the engine and then mirrors this
  workspace of the same commit over prod's (`scripts/prod/mirror_workspace.py`). The mirror writes the
  repo's files and deletes the ones it wrote before and the repo dropped (`.mirrored-files` lists
  them); prod's own files are never touched: `data/`, `.env`, `dataforge.db`, `report/`, `triggers/`,
  `deploy/.ledger/` and `config/investments.yaml`.

## What is precious and what is not

| Data | Rebuildable? | How |
|---|---|---|
| `data/raw/` | **No.** Pluggy only returns a short window of history | Backed up weekly by the `backup` job |
| `data/edicao.db` (categories, rules and corrections you edit) | **No** | Same backup |
| `data/extratos/` (the bank statement PDFs you drop there) | **No.** The bank's app exports them | Same backup |
| `data/cleansed/` | Yes | `scripts/reprocess.py --source raw --target cleansed --entity all` |
| `data/curated/` | Yes | Dropped and recreated on every run (`finance_daily` or `reclassificar`) |
| `data/bridge/`, `report/` | Yes / ignored | Workbook era, not maintained |

Rules that follow from this table:

- **Never write to `data/raw/` or `data/edicao.db` from an experiment, a test or a one-off script.**
  Only `landing/landing2raw.py` writes raw (new partitions only, never rewrites), and only the app or
  `scripts/report/carga_inicial_edicao.py` writes `edicao.db`. A change to `landing2raw.py` deserves
  extra care: it is the one script whose bug cannot be undone by a rerun.
- Before a risky change near raw (a migration, a rename), run the backup by hand first:
  `dataforge run GetFinance backup`.
- Breaking cleansed or curated is cheap: fix the code and rebuild. Don't hold back on that.
- Don't run experiments at the time of the daily trigger (`triggers/daily.yaml`): two jobs writing the
  same tables at once is not yet queued (DataForge df-1mdh.10).

## Backup

`jobs/backup` (trigger `triggers/backup.yaml`, weekly) zips `data/raw/`, a consistent copy of
`data/edicao.db` and the statement PDFs of `data/extratos/` into `../../../_backups/GetFinance/` (relative to the prod workspace, so
`projects/_backups/GetFinance/` on the same disk, outside every repo) and keeps the newest few. The
destination and how many to keep are params in `jobs/backup/job.yaml`. To restore, unzip `raw/` over
`data/raw/`, `edicao.db` over `data/edicao.db` and `extratos/` over `data/extratos/`, then rebuild cleansed
with `reprocess.py`.

## Bank statements (df-z4fh.5)

The bank only exports the checking-account statement as a PDF. Save each one, as the bank's app exports
it, in `data/extratos/` of the prod workspace (create the folder the first time; any file name ending in
`.pdf`). The next `finance_daily` reads them all into `curated.extrato` (one row per entry) and
`curated.extrato_saldos` (each daily balance and whether it closes with the entries), and the weekly
backup keeps them. Overlapping periods are fine: a day is taken from one file only. A PDF the task cannot
read, or whose agency and account match no BANK account, is left out (the others are still read) and the
run fails naming it and why: move it out and tell a session. The reading needs `pypdf`, a DataForge
dependency (`uv.lock`); without it the task only warns and the tables stay empty.
Then every suspect transaction gets the statement's verdict (`curated.veredito_suspeitos`: in the
statement, not in it, or not covered), the statement lines with no transaction go to
`curated.extrato_sem_lancamento`, and `curated.acerto_por_motivo` counts how often each suspect reason is
right (df-z4fh.3).
The job `extrato` runs only these two tasks, on demand and without calling the bank: it reads the
folder again and redoes the verdict over the data the last `finance_daily` left (df-7xk9.1).
Statements hold real names and amounts: never put one in git, a canvas or a public repo.

## Report logic and the editable tables (since 2026-11-09, df-cns)

The Excel workbooks under `report/` are **ignored**: not maintained, not deleted. Don't read, refresh
or edit them for report work, and don't reason about the report from `docs/workbook.md` (history). The scripts
that refreshed them (`export_bridge.py`, `build_report.py`) were removed (DataForge df-wfiu); git keeps them.
The report logic lives in `scripts/report/`:

- pure modules: `classificacao.py` (category routing E3/E3a), `saude.py` (Saúde do mês + breakdown),
  `cartao_listas.py` (active installments, next-bill items), `report_lib.py` (cartão, investimentos,
  liquidez), `edicao.py` (the editable tables);
- task scripts: `classify.py` → `curated.fact_transaction_classified` and `curated.edicao_aplicada`
  (the `versao` of `edicao.db` it used); `report_curated.py` →
  `curated.report_*`. Both run at the end of `finance_daily` and, alone, as the job `reclassificar`
  (no Pluggy), which also rebuilds `curated.conferencia_saldo` and `curated.lancamentos_suspeitos` so an
  interval marked as explained shows after Aplicar.

Categories (`categorias`), rules (`mapeamento`), per-transaction corrections (`correcoes`, Pluggy id
→ subcategory; a correction beats every other step, `etl_tag` included) and explained divergences of the
balance check (`explicacoes`: account, interval end, the `diferenca` at marking, reason) are user data in
`data/edicao.db` (connection `edicao`), with `versao`, a counter every write bumps. Their content has
real names: never put it in git, a canvas or a public repo. A correction reaches every report that
reads categories: `report_saude*`, the classified list, the bill items and the investment flows of
`report_investimentos` (a row corrected to `Investimentos` counts as applied, one corrected away from
it no longer does). `report_cartao` and `report_liquidity` come from balances and installments.

## Leak guard

DataForge's `scripts/leakguard.py` guards this workspace too (it took in the guard this workspace
had, df-bsb2.2.2). It runs in pre-commit, on the staged files and on the commit message, and in CI
(job `leak-scan`, with gitleaks). It blocks private files (here, in any case: `data/`, `report/`,
`docs/archive/`, `docs/workbook.md`, `config/investments.yaml`, an archive such as a `.zip` of a
backup, and any binary file other than the app's fonts and images; anywhere: a `.env` other than
`.env.example`, a `.db`/`.parquet`/`.xlsx`), CPF/CNPJ with valid check digits, account short codes and personal
e-mails. On the owner's machine it also blocks every **real value** read from the real `data/`
(Pluggy ids, account numbers, owner and counterparty names, documents; the person or shop named in a
transaction's description, an amount with cents from 100 up, a card's last four digits next to
"final", `cardNumber` or `****`), `data/edicao.db` (category rules) and the `.env` beside it. A
round amount, a small one and the bank's own wording ("Pix enviado", "PAGAMENTO RECEBIDO") pass, so
a test may use those. Examples in code, docs and tests use fake values only (e.g.
`901/0001/00012345-6`); a real account number in a docstring example reached the public DataForge
repo this way (df-k37o). Audit the whole history with `scripts/leakguard.py --history`.

Where the real values come from (any of them; the guard reads them all):

- `GETFINANCE_DATA=<workspace>/data`, set on the command (`GETFINANCE_DATA=<workspace>/data git
  commit ...`): the `data/` of the workspace with the real data, with `raw/` in it.
- DataForge's gitignored `.leakguard-workspaces` at the main checkout's root: one workspace
  directory per line.
- This folder's `data/` in the main checkout, found with nothing set, when it holds `raw/` or
  `edicao.db` and is not the demo's synthetic data (`data/raw/SYNTHETIC`).

With none of them, the guard **fails** outside CI and says what to set. Whoever has no real data at
all (another contributor, a fresh clone) declares it with `LEAKGUARD_NO_REAL_DATA=1` on the command
or in the shell; an unset variable never counts as "no real data". CI needs neither: it has no real
data by design.

## Tests

The pipeline tests live here, in `tests/`, with their own `pytest.ini`. They run on DataForge's
venv (`uv sync`; the engine is the checkout's own `dataforge/`, no pin), from the DataForge root:
`python -m pytest workspaces/GetFinance -m 'not integration'`. They cannot share one pytest run with
DataForge's `tests/` (both are a package named `tests`), so the engine's tests are a second command,
`python -m pytest tests -m 'not integration'`. CI runs these in their own job, `tests-getfinance`,
whose coverage goes to SonarQube Cloud with the engine's, and again on Windows in
`tests-getfinance-windows` (production runs Windows Python), which the `tests-windows` check gates.

- **Each checkout tests itself.** `tests/_delta_util.py` gives `ROOT` and `SCRIPTS` (this folder in
  this checkout, a worktree or the main one) and `REAL_DATA`. When the environment variable
  `GETFINANCE_DATA` is set, `REAL_DATA` is the directory it names: the prod workspace's `data/`
  (with `raw/`), read-only; it wins over any `data/` in the checkout. Without it, `REAL_DATA` is
  `data/` of this folder in the main checkout, since a worktree has none. The variable lasts per
  shell or per command (a tool that runs each command in a fresh shell drops an `export`), so put it
  on the command: `GETFINANCE_DATA=<workspace>/data python -m pytest workspaces/GetFinance -m integration`.
  No hardcoded workspace paths.
- **Two tiers.** Deterministic tests build their data in `tmp_path` and run in CI
  (`-m "not integration"`). Tests that read the real data are marked `integration`, read
  `REAL_DATA` and never write it; they skip where there is none (CI, a fresh clone). Run everything
  locally with `GETFINANCE_DATA` set.
- **Fake values only.** A test never pins a real account, agency, document, name or id: the leak guard
  blocks the commit. Build examples from fakes (`901/0001/00012345-6`, CPF/CNPJ with invalid check digits).
- `tests/test_job_wiring.py::test_finance_daily_warning_does_not_fail_the_run` is an expected
  failure (`xfail`, strict) on the current engine until the owner decides df-vlfl.

## Git

This workspace is a folder of the public DataForge repo (df-bsb2.2): same rules as the rest of it.
Change it through a branch and a PR from a DataForge worktree, never by committing to `main`. CI is
the gate; on every PR, read what SonarQube reports even when its check is green. Real data never
enters git: the leak guard and the `.gitignore` keep `data/`, `.env` and the real config out.
