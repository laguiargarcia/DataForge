# DataForge

A local, Databricks-inspired DAG orchestrator. Includes **GetFinance-Show**: a synthetic
personal-finance pipeline that runs end-to-end with no bank account and no credentials —
just clone, install, and run.

```
synthetic data ──▶ raw ──▶ cleansed ──▶ curated (Delta tables) ──▶ bridge CSVs ──▶ Excel report
                   └────────── dataforge run GetFinance-Show finance_daily ──────────┘
```

---

## Quickstart (zero credentials)

```bash
python -m venv .venv
.venv/bin/pip install -e .           # Windows: .venv\Scripts\pip install -e .
```

> **Project folder:** for now, its full path must have no spaces and no accented characters.
> The pipeline's Delta reads fail on such paths.

> **Required — set `DATAFORGE_HOME` before running:**
>
> ```bash
> export DATAFORGE_HOME="$(pwd)"
> # Windows PowerShell: $env:DATAFORGE_HOME = (Get-Location).Path
> ```
>
> The engine resolves workspaces as `$DATAFORGE_HOME/workspaces/<name>`. Without this
> variable it looks in the OS data directory (`~/.local/share/DataForge`), not the clone,
> and the run fails with `Workspace 'GetFinance-Show' not found`.

Run the full pipeline:

```bash
dataforge run GetFinance-Show finance_daily
```

This produces curated Delta tables under `workspaces/GetFinance-Show/data/` using
synthetic data — no bank, no Pluggy, no secrets.

### Open the Finanças app

After the run, start the server:

```bash
dataforge serve
```

It prints the app URL, by default <http://127.0.0.1:8000/apps/GetFinance-Show/financas/>.
Open it in a browser. No Node is needed: the compiled app (`dist/`) ships in the repo.

The app only reads and formats: the numbers come from the job's `classify` and `report` tasks
(`curated.report_*` and `curated.fact_transaction_classified`). Category names and their groups
(Essencial, Não Essencial, …) come from the editable tables in `data/edicao.db`, seeded on the
first run from `config/edicao/*.csv`; re-run only those two tasks with
`dataforge run GetFinance-Show reclassificar`.

If you change the app sources in `workspaces/GetFinance-Show/apps/financas/src/`, run
`npm install` and `npm run build` in that folder to regenerate `dist/`. Before committing, run
`npm run build` and check whether `dist/` changed.

---

## Bring your own bank (optional)

To connect a real bank via [Pluggy](https://pluggy.ai) (Open Finance Brazil), create a
**private workspace** — private workspaces are gitignored and never published:

1. Copy the example workspace and fill in your credentials:
   ```bash
   cp workspaces/GetFinance-Show/.env.example workspaces/MyBank/.env
   cp -r workspaces/GetFinance-Show/config/*.example.yaml workspaces/MyBank/config/
   # edit workspaces/MyBank/.env — add CLIENT_ID, CLIENT_SECRET, ITEM_IDS
   ```
2. Run with your workspace name:
   ```bash
   dataforge run MyBank finance_daily
   ```

The `.gitignore` blocks `workspaces/*/` directories that contain a `.env`, so real
credentials and real data never leave the machine.

---

## Security

No secrets are committed. A pre-commit leak guard (gitleaks + a PII/private-path block,
see `scripts/leakguard.py` and `.pre-commit-config.yaml`) plus a CI leak-scan keep real
data out of the repository.

---

## More

- **CLI reference:** `dataforge --help`
- **Architecture & subsystems:** `docs/architecture.md`, `docs/`

---

MIT License — Copyright (c) 2026 Lucas Aguiar Garcia
