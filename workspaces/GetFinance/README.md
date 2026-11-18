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
start over on a checkout without real data, delete `data/`. The synthetic fetch writes only into an empty `data/raw/` or one it
wrote itself (the file `data/raw/SYNTHETIC`), and fails before writing anything otherwise, so on a
checkout with real data the demo stops at `fetch` and changes nothing. `tests/test_finance_demo.py`
keeps every other task equal to `finance_daily`'s: change a task in both job files.

## Where it lives

- **Development** happens in a clone on the Linux disk, inside the DataForge dev clone, at
  `workspaces/GetFinance` (DataForge df-aef7.1). Each change is made in a worktree outside
  `workspaces/` (DataForge's `.worktrees/getfinance/<issue>`), so DataForge does not discover it as
  a workspace. The clone has no `data/`: the real data stays in prod, read through `GETFINANCE_DATA`
  (see Tests). The commands are in DataForge's `docs/commands.md`, section "GetFinance dev clone".
- **Prod** is the checkout under the Windows DataForge folder (`workspaces/GetFinance`), run daily
  by the Task Scheduler. It holds the real `data/` and `.env`. Nobody edits code there: it receives
  merged changes with `git pull --ff-only`, outside the daily trigger's time (`triggers/daily.yaml`).

## What is precious and what is not

| Data | Rebuildable? | How |
|---|---|---|
| `data/raw/` | **No.** Pluggy only returns a short window of history | Backed up weekly by the `backup` job |
| `data/edicao.db` (categories and rules you edit) | **No** | Same backup |
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

`jobs/backup` (trigger `triggers/backup.yaml`, weekly) zips `data/raw/` plus a consistent copy of
`data/edicao.db` into `../../../_backups/GetFinance/` (relative to the prod workspace, so
`projects/_backups/GetFinance/` on the same disk, outside every repo) and keeps the newest few. The
destination and how many to keep are params in `jobs/backup/job.yaml`. To restore, unzip `raw/` over
`data/raw/` and `edicao.db` over `data/edicao.db`, then rebuild cleansed with `reprocess.py`.

## Report logic and the editable tables (since 2026-11-09, df-cns)

The Excel workbooks under `report/` are **ignored**: not maintained, not deleted. Don't read, refresh
or edit them for report work, and don't reason about the report from `docs/workbook.md` (history).
The report logic lives in `scripts/report/`:

- pure modules: `classificacao.py` (category routing E3/E3a), `saude.py` (Saúde do mês + breakdown),
  `cartao_listas.py` (active installments, next-bill items), `report_lib.py` (cartão, investimentos,
  liquidez), `edicao.py` (the editable tables);
- task scripts: `classify.py` → `curated.fact_transaction_classified`; `report_curated.py` →
  `curated.report_*`. Both run at the end of `finance_daily` and, alone, as the job `reclassificar`
  (no Pluggy).

Categories (`categorias`) and rules (`mapeamento`) are user data in `data/edicao.db` (connection
`edicao`). Their content has real names: never put it in git, a canvas or a public repo.

## Leak guard

DataForge's `scripts/leakguard.py` guards this workspace too (it took in the guard this workspace
had, df-bsb2.2.2). It runs in pre-commit and in CI (job `leak-scan`, with gitleaks), and blocks
private files (here: `data/`, `report/`, `docs/archive/`, `docs/workbook.md`, `config/investments.yaml`,
`config/merchant_normalization.local.yaml`; anywhere: a `.env` other than `.env.example`, a
`.db`/`.parquet`/`.xlsx`), CPF/CNPJ with valid check digits, account short codes and personal
e-mails. On the owner's machine it also blocks every **real value** read from the real `data/`
(Pluggy ids, account numbers, owner and counterparty names, documents), `data/edicao.db` (category
rules) and the `.env` beside it. It finds them through `GETFINANCE_DATA` (this workspace's real
`data/`, set on the command) and through the workspaces listed in DataForge's gitignored
`.leakguard-workspaces`. Examples in code, docs and tests use fake values only (e.g.
`901/0001/00012345-6`); a real account number in a docstring example reached the public DataForge
repo this way (df-k37o). Audit the whole history with `scripts/leakguard.py --history`.

## Tests

The pipeline tests live here, in `tests/`, with their own `pytest.ini`. They run on DataForge's
venv (`uv sync`; the engine is the checkout's own `dataforge/`, no pin), from the DataForge root:
`python -m pytest workspaces/GetFinance -m 'not integration'`. They cannot share one pytest run with
DataForge's `tests/` (both are a package named `tests`): CI runs them in their own job,
`tests-getfinance`, whose coverage goes to SonarQube Cloud with the engine's.

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
