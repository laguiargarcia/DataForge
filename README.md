# DataForge

A local, Databricks-inspired DAG orchestrator. Includes **GetFinance**: a personal-finance
pipeline whose demo job runs end-to-end on synthetic data, with no bank account and no
credentials — just clone, install, and run.

```
synthetic data ──▶ raw ──▶ cleansed ──▶ curated (Delta tables) ──▶ report tables ──▶ Finanças app
                   └────────── dataforge run GetFinance finance_demo ──────────┘
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
> and the run fails with `Workspace 'GetFinance' not found`.

Run the full pipeline on synthetic data:

```bash
dataforge run GetFinance finance_demo
```

This produces curated Delta tables under `workspaces/GetFinance/data/` using
synthetic data — no bank, no Pluggy, no secrets. To start over, delete that `data/`.
The workspace's own `README.md` says what the demo runs and how its tests work.

### Open the Finanças app

After the run, start the server:

```bash
dataforge serve
```

It prints the app URL, by default <http://127.0.0.1:8000/apps/GetFinance/financas/>.
Open it in a browser. No Node is needed: the compiled app (`dist/`) ships in the repo.

The app only reads and formats: the numbers come from the job's `classify` and `report` tasks
(`curated.report_*` and `curated.fact_transaction_classified`). Category names and their groups
(Essencial, Não Essencial, …) come from the editable tables in `data/edicao.db`, seeded on the
first run from `config/edicao/*.csv`; re-run only those two tasks with
`dataforge run GetFinance reclassificar`.

If you change the app sources in `workspaces/GetFinance/apps/financas/src/`, run
`npm install` and `npm run build` in that folder to regenerate `dist/`. Before committing, run
`npm run build` and check whether `dist/` changed.

### Workspace apps (own window)

A workspace app can also open in its own window, without a browser, and can ship an optional Python
backend. With the GetFinance workspace:

```bash
dataforge app GetFinance financas            # opens the window (starts the local server if needed)
dataforge app GetFinance financas --atalho   # Windows: creates a Desktop shortcut instead
dataforge stop                               # shuts the local server down
```

`dataforge gui` and `dataforge app` share one local server, which stops by itself about a minute after
the last window closes. Details: `docs/commands.md`, `docs/workspace-layout.md`.

---

## Bring your own bank (optional)

The same workspace has the real daily job, `finance_daily`, which fetches your accounts from a
real bank via [Pluggy](https://pluggy.ai) (Open Finance Brazil):

1. Delete the demo's `workspaces/GetFinance/data/` first: the real fetch refuses a raw layer the
   demo wrote, and the demo's editable tables and curated tables are synthetic too.
2. Copy the example settings and fill in your credentials:
   ```bash
   cp workspaces/GetFinance/.env.example workspaces/GetFinance/.env
   cp workspaces/GetFinance/config/investments.example.yaml workspaces/GetFinance/config/investments.yaml
   # edit workspaces/GetFinance/.env — add CLIENT_ID, CLIENT_SECRET, ITEM_IDS
   ```
3. Run the daily job:
   ```bash
   dataforge run GetFinance finance_daily
   ```

The `.env`, `data/` and your local config are gitignored, so real credentials and real data
never leave the machine. Every file a real run needs is in `workspaces/GetFinance/docs/commands.md`
("Configuration").

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
