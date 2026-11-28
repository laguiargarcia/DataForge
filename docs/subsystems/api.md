---
topic: API
read_when: "When working on the HTTP / FastAPI surface (routers, the app backend proxy, `/health`, `/api/instance` presence and shutdown)"
subsystem: api
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-0lf, df-37a]
code:
  - dataforge/api
status: pronto
---

# API

## API surface

<!-- AUTO:overview -->
<!-- AUTO-END:overview -->

## How to use

<!-- MANUAL:how-to-use -->
The API is a **FastAPI** app (`dataforge/api/__init__.py`, `app = FastAPI(...)`) served
by **uvicorn**. In the desktop app it runs embedded in a background thread
(`dataforge/gui/daemon.py`); standalone it can be served via `uvicorn dataforge.api:app`.

Routers live under `dataforge/api/routers/` and are mounted in `__init__.py`:

- `workspaces` — `GET /api/workspaces/` (list) and per-workspace execution history.
- `jobs` — under `/api/workspaces`: list jobs/scripts, `POST .../jobs/{job}/run` (202),
  read/create jobs, schedule lookup.
- `executions` — under `/api/executions`: `GET /{run_id}/stream` (SSE) and `/{run_id}/output`.
  The stream polls every 1 s and sends one `data: {run_id, status, tasks}` snapshot per tick, each
  task `{name, status, started_at, finished_at}` in `inicio` order, plus `output` only when this
  connection has not yet sent that row's current output (df-ahua.16.1): a task stores its output
  once, when it ends (an orphan's also gets `orphans.REASON` appended, and that change is sent), and
  `execucoes.output` has no size limit, so each output crosses a connection once instead of every
  second. An absent `output` means "unchanged"; a new connection (a finished run opened from History)
  gets every output in its first snapshot. It closes after the snapshot with `success`/`failed` and
  when the run row disappears. It never reconciles orphans: a run whose process died while the server
  is up stays `running` there and the stream stays open until another read reconciles it
  (df-ahua.16.11). The run detail panel is its only client; `tests/test_gui_live_api.py` checks that
  every field the panel reads is one the stream sends.
- `stream` — `GET /api/workspaces/{ws}/stream` (per-workspace SSE event feed).
- `triggers` — under `/api/workspaces`: CRUD + `reconcile` for OS-scheduled triggers.
- `deploy` — under `/api/workspaces`: pipelines, plan, status, `apply`, `rollback`.
- `apps` — mounted at `/apps`: static publication of workspace web apps.
  `GET /apps/{workspace}/{app}` redirects (307) to the trailing-slash form;
  `GET /apps/{workspace}/{app}/{path}` serves the file from `<workspace>/apps/<app>/dist/`
  (`Cache-Control: no-cache`). A path with no match and no file extension falls back to
  `dist/index.html` (SPA); a missing file with an extension, path traversal, an unknown
  workspace or an invalid app (see `dataforge/apps.py`) returns 404. So does, before any disk
  access, an app name outside `[A-Za-z0-9_-]+` or a path that starts with `/` or contains
  `\`, `:` or NUL: on Windows these are drive, UNC or stream paths.
- `tables` — under `/api/workspaces`: `GET /{ws}/tables/{table}` reads a curated Delta table
  (`curated_dir(ws)/<table>`, name `[A-Za-z0-9_]+`; unknown workspace/table -> 404). The read (schema,
  filters, collect) runs on `dataforge.delta_pool`, never on the request thread: a Delta read on a thread
  that then ends crashes the Windows process (df-5lk8; see Subsystem-specific rules).
  Query params, all optional and repeatable except `limit`: `eq`, `gte`, `lte` as
  `column:value` (split at the first `:`, value cast to the column type; `Date` takes
  `YYYY-MM-DD`, `Boolean` takes `true`/`false`), and `limit` (1..100000, default 100000).
  Bad filter, unknown column or bad value -> 422. Response:
  `{"columns": [...], "rows": [{...}], "truncated": bool}`; NaN and +/-inf floats become `null`.

- `instance` — under `/api/instance` (`routers/instance.py`), used by the GUI, `dataforge app` and `dataforge stop`:

  - `POST /api/instance/presence` — body `{client_id, kind: "admin"|"app", workspace?, app?}`; `kind: "app"`
  needs a valid workspace and app name (422 otherwise). Registers or refreshes that window in
  `presence.registry`; 204. Windows repeat it every 30 s; one silent for 120 s expires.
  - `DELETE /api/instance/presence/{client_id}` — the window closed; 204 (also for an unknown id).
  - `POST /api/instance/shutdown[?force=true]` — asks the hosting process to stop. `202 {"ok": true}` when it
  accepted. `409 {"detail": "há job rodando"}` when a job runs in this server and `force` is not set;
  `409 {"detail": "este servidor não pode ser parado por aqui; use Ctrl+C"}` when the process registered
  no stop handler (`serve --reload`, or a plain `uvicorn dataforge.api:app`). The handler only signals; the
  process stops after the 202 is sent.

Top-level: `GET /health` and `POST /api/triggers/notify` (fire-and-forget daemon wake hint
from the CLI after a scheduled run).

**`/health` identity.** `GET /health` returns `{"status": "ok", "service": "dataforge", "version": <sha256
fingerprint of the package's *.py files: path, size, mtime>, "home": <resolved data dir>}`. Clients
(`instance.probe`) use it to tell this DataForge from another program (`service`), another home
(`home`) or an older running version (`version`).

**Security: no login on `instance` either.** With `dataforge serve --host 0.0.0.0` (Host check off), any
client on the LAN can `POST /api/instance/shutdown` and stop the server, just as it can already trigger
jobs. This is the same "no login" exposure described below; the Origin check blocks only browsers.
Bind to loopback unless you accept it.

**Host and Origin.** There is no login. `HostOriginMiddleware` (`dataforge/api/__init__.py`)
answers 403 to any request whose Host name is outside `compat.allowed_hosts()` (loopback plus
the comma-separated `DATAFORGE_ALLOWED_HOSTS`; `*` turns the Host check off), and to any method
other than GET, HEAD and OPTIONS whose `Origin` differs from `Host` in host:port (`Origin: null`
counts as different). No `Origin` passes, since the CLI and Qt send none. `dataforge serve --host`
sets the variable; a plain `uvicorn dataforge.api:app --host X` must set it by hand.

**Streaming (SSE).** `broadcaster.py` holds a per-workspace `WorkspaceBroadcaster`
(`get_broadcaster(workspace)`); each SSE subscriber gets its own `asyncio.Queue` via
`subscribe()`. Run-status events are pushed with `publish(...)` — including from the
FastAPI `lifespan` startup scan, which re-surfaces `JobRunORM` rows still `status="running"`.

**Orphaned runs (df-bw7).** The lifespan first runs `orphans.reconcile_or_log()`: runs and task rows
whose owner process has provably ended become `failed` with a reason (rules in
`subsystems/engine.md`, "Run states and orphaned runs"); only the runs left `running` (alive, or not
judgeable) are published. `GET /{ws}/executions` and `GET /api/workspaces/` reconcile the same way
before reading, so a dead run never shows in progress while the daemon keeps running. Each
`executions` item carries `reason` (null unless the run was interrupted). A failing reconciliation is
logged and never blocks startup or the read.

**App backend proxy.** `/apps/<ws>/<app>/api/<path>` (any of GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS,
registered before the static catch-all in `routers/apps.py`) is forwarded to the app's own ASGI
backend (`supervisor.target(info)` starts or reuses it). Only the workspace and app names are
validated; the path goes to the backend untouched (`raw_path` after `/api/` and the raw query bytes,
so `:`, `;`, `%2F` etc. arrive as sent). The bare `/apps/<ws>/<app>/api` goes to the backend too
(path `/`). Headers travel as bytes both ways. Dropped from the request: `host`, `content-length`,
`accept-encoding`, hop-by-hop headers (incl. `proxy-connection` and any header named in
`Connection`) and the client's `x-dataforge-app-token`, replaced by the real per-start secret. Dropped
from the response: hop-by-hop headers (and those named in `Connection`), `date`, `server` (uvicorn adds
its own), `content-length` and `content-encoding` - httpx decodes the body, so the client gets it
decoded; repeated headers such as `Set-Cookie` are kept. Bodies are buffered in memory, not streamed.
One shared `httpx.AsyncClient` (no env proxies, no redirects, 5 s connect / 300 s read timeout; a
timeout answers 504). An app without `backend` answers 404. If the backend cannot start, refuses the
connection (stopped by a concurrent sweep) or drops it mid-answer, the answer is
`503 {"detail": <reason>, "log": <path to logs/app_<app>.log>}`; the server itself stays up. Because
the api route comes first, **an app's `dist/` cannot have an `api/` folder**: those URLs never reach
the static files. The Host/Origin check applies to the proxy like any other route.
Lifespan: a task runs `supervisor.sweep(now, presence.registry)` every 10 s (`SWEEP_SECONDS`, in a
thread) and `supervisor.stop_all()` runs at shutdown. A server that dies before its lifespan ends
still leaves no backend: each one runs under `dataforge.app_backend_host`, which stops it when the
pipe on its stdin (write end held only by the server) reaches EOF (df-xy7c).
<!-- MANUAL-END:how-to-use -->

## Subsystem-specific rules

- **Delta reads go through `dataforge.delta_pool.read(path, build)`** (df-5lk8). On Windows (polars
  2.1.0 + deltalake 1.6.6), a process whose first Delta `collect` ran on a thread that then ends dies
  tens of reads later with an access violation in `deltalake/_internal.pyd`; the request threads of
  FastAPI/anyio end after a few idle seconds, and the prod server died that way 7 times on 2026-11-21.
  The pool's 4 threads live as long as the process, so the first read never happens on a thread that
  ends. The quality gate bans `pl.scan_delta`/`pl.read_delta` in the package outside `delta_pool.py`
  (TID251), and in workspace app backends (DF003). Reproduced only on Windows, and only with a table of 2+ commits; Linux never crashed.
  `tests/test_tables_api.py::test_leituras_em_threads_que_terminam_nao_derrubam_o_servidor` is the
  regression (it crashes on Windows without the pool).

<!-- MANUAL:rules -->
- Persistence is **SQLAlchemy over SQLite** (see `dataforge/db.py`), reached through
  `dataforge.compat.global_db_url()`. There is **no DuckDB anywhere in `dataforge/api/`** —
  do not reintroduce the old "endpoints open DuckDB read_only" model.
- There is **no CORS middleware**: workspace apps are served from the API's own origin
  (`/apps/...`) and call `/api/...` same-origin. Host and Origin checks are described above;
  auth is deferred to Phase 4.1.
<!-- MANUAL-END:rules -->

## Component diagram

<!-- AUTO:mermaid -->
<!-- AUTO-END:mermaid -->

## Past learnings

<!-- LEARNINGS:AUTO -->
- 2026-05-19 (CORRECTED 2026-06-22): the API is SQLAlchemy/SQLite (`dataforge/db.py`), NOT DuckDB — there is no DuckDB in `dataforge/api/`. The original "endpoints open DuckDB read_only" claim was stale. <!-- obs:918 -->
- 2026-05-19: launchd plist generation XML-escapes workspace/trigger names to avoid breakage/injection (WR-01). <!-- obs:904 -->
<!-- LEARNINGS:AUTO-END -->
