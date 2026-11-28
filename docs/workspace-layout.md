---
topic: Workspace Layout & YAML Reference
read_when: When dealing with workspace paths, per-workspace data, or YAML reference for workspace/job/task definitions
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-762, df-b95, df-oe8, df-t7v]
code:
  - dataforge/cron.py
  - dataforge/models.py
  - dataforge/parser.py
  - dataforge/yamlio.py
status: pronto
---

# Workspace Layout & YAML Reference

## Disk layout

```
workspaces/<name>/
  workspace.yaml
  connections/connections.yaml
  jobs/<job_name>/
    job.yaml          # name + retry config + env_file + tasks list (inline)
  scripts/<name>.py   # script path is relative to workspace root
  apps/<app>/         # optional web apps (see below)
    app.yaml
    dist/index.html   # compiled bundle served by `dataforge serve`
  logs/execucao_<id>_<job>_<task>.log
  dataforge.db        # execution history (SQLite via SQLAlchemy)
  <db_name>.duckdb    # DuckDB databases (e.g. raw.duckdb, cleansed.duckdb)
```

Log files named: `execucao_{id}_{job_name}_{task_name}.log`

`--workspace` resolves to `Path("workspaces") / name` from project root. When omitted, uses cwd if `workspace.yaml` exists there.

## Task YAML — inline in `job.yaml`

```yaml
name: finance_daily
env_file: .env          # optional: loaded into subprocess env

retry:
  attempts: 2
  delay_seconds: 5

tasks:
  - name: landing2raw
    script: scripts/landing2raw.py
    params:             # passed as --key value CLI args to the script
      target: raw
    depends_on: []
    active: true        # optional, default true; false = skip, shown as None in results

  - name: raw2cleansed_accounts
    script: scripts/raw2cleansed.py
    params:
      table: accounts
      source: raw
      target: cleansed
    depends_on: [landing2raw]

  # Job-reference task
  - name: run_other_job
    job: other_job_name   # calls another job as a subtask
    depends_on: []
```

## Web apps — `apps/<app>/`

A workspace may ship static web apps. `dataforge serve` discovers every
`workspaces/<ws>/apps/<app>/` and publishes its `dist/` at `/apps/<ws>/<app>/`.
An app is valid only if all of these hold; otherwise it is skipped with a logged warning:

- The directory name `<app>` matches `[A-Za-z0-9_-]+`.
- `app.yaml` is a YAML mapping.
- `name` equals the directory name `<app>`.
- `dist/index.html` exists (the compiled bundle is what is served; no Node needed to run).
- `backend`, if present, has the form `module:object` (`[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*`).

```yaml
name: financas            # required, must match the directory name
title: Finanças           # optional, shown by `dataforge serve`; defaults to name
icon: icon.ico            # optional, relative to the app folder (window and shortcut)
backend: backend.main:app # optional: the backend's ASGI app, as module:object
```

An `icon` that is missing or resolves outside the app folder is ignored with a logged warning
(the app stays valid). Without a usable `icon` the app window shows the DataForge default icon (a
blue square with a white "D", the same as the tray icon). An app without `backend` stays static.

Sources (`src/`, `package.json`, ...) live next to `dist/`; only `dist/` is served.

To promote an app with `dataforge deploy`, add `{source: 'apps/**', target: 'apps/'}` to the
pipeline's `pairs`. Files are copied byte-for-byte (binary assets included) with kind `app`.
`node_modules/` and `test-results/` are deploy-ignored by default, as `__pycache__/` is.
`dist/` does travel, so rebuild it in the source workspace before deploying.

### Writing a backend

A `backend` runs as its own `uvicorn` process (`python -m dataforge.app_backend_host <module:object>
<free port>`, on 127.0.0.1, working directory = the app folder), started on demand by the first request to
`/apps/<ws>/<app>/api/...` and restarted if it died. It never outlives the DataForge server that
started it: its stdin is a pipe that server holds, and when the server ends in any way (a crash,
Stop-Process included) the backend stops within a few seconds (df-xy7c). So a backend must not read stdin.

The backend adds the secret check in one line, so it answers only calls that came through DataForge:

```python
from fastapi import FastAPI
from dataforge.app_guard import AppTokenMiddleware

app = FastAPI()
app.add_middleware(AppTokenMiddleware)
```

`AppTokenMiddleware` compares the `X-DataForge-App-Token` header with `DATAFORGE_APP_TOKEN` and answers
403 (`{"detail": "acesso só pelo DataForge"}`; a websocket is closed with code 1008) otherwise. It
**fails closed**: started without `DATAFORGE_APP_TOKEN` in its environment (e.g. run by hand with
`uvicorn`), it refuses every call.

- **Read Delta through `dataforge.delta_pool.read(path)`**, not `pl.read_delta`/`pl.scan_delta`: a
  backend reads on request threads that end, and on Windows a Delta read on a thread that ends crashes
  the process a few dozen reads later (df-5lk8; `docs/subsystems/api.md`, Subsystem-specific rules).
  The quality gate refuses `read_delta`/`scan_delta` under `backend/` (DF003, df-vpo3).
- **Do not write files under the app's own `backend/` folder.** The supervisor fingerprints every file
  there (path, size, mtime; `__pycache__` excluded), checks it at most every 3 s on a call, and restarts
  the backend when it changes. A backend that writes into `backend/` (a cache, a sqlite file, a log) makes
  itself restart in a loop. Write data under the workspace's `data/` (`$DATAFORGE_WORKSPACE_DIR/data`).
- **Environment.** The process inherits the user's environment plus `DATAFORGE_HOME`,
  `DATAFORGE_WORKSPACE`, `DATAFORGE_WORKSPACE_DIR`, `DATAFORGE_API` (`http://127.0.0.1:<api port>`) and
  `DATAFORGE_APP_TOKEN` (a per-start secret; the proxy adds it as `X-DataForge-App-Token` and drops any
  value the client sends). It does **not** get the workspace `.env`: read secrets some other way.
- **Lifetime.** Stopped after 15 minutes without a proxied call (`IDLE_SECONDS`), when all its windows
  closed (once a window had been seen), and when DataForge shuts down. The next call starts it again.
  If it cannot start (20 s limit), the proxy answers 503 with the reason.
- **Log.** `<workspace>/logs/app_<app>.log`, stdout and stderr together, overwritten on every start.

### Shortcut icon

`dataforge app <ws> <app> --atalho` creates a Windows shortcut. For its icon, use a `.ico` file as
`icon:` in `app.yaml`; any other format (png, svg) leaves the default shortcut icon and the command
prints a note.
