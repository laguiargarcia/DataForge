---
topic: Commands
read_when: When invoking project CLI / scripts, running tests, or the API server
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-3qd, df-3ul]
---

# Commands

## Setup (dev home on the Linux disk; ADR 0005)

The core is developed in `~/src/DataForge` (ext4), never in the Windows folder, which is prod only.

```bash
# Once: the clone and its venv (uv; Python from .python-version, versions from uv.lock; ~2 s warm)
git clone git@github.com:<owner>/DataForge.git ~/src/DataForge && cd ~/src/DataForge
uv sync --frozen                                      # runtime + groups dev and quality, project editable
.venv/bin/pre-commit install                          # gitleaks, leakguard, quality gate
printf '%s\n' <win>/workspaces/GetFinance > .leakguard-workspaces   # commits checked against its real values

# Per task: a worktree with its own venv (uv sync --frozen of its own uv.lock; ~1 s + fetch)
scripts/dev/new-worktree.sh <issue> <branch> [<base>]  # base defaults to origin/main
# Done (after the PR merged or the work was dropped):
git worktree remove .worktrees/<issue> && git branch -D <branch>
```

A uv venv has no `pip`. Dependencies live in `pyproject.toml` (runtime, and the `dev` and `quality`
groups) and are pinned in `uv.lock`, the one list of versions dev, CI and prod install (df-n8ac.12).
Change one with `uv add` / `uv lock --upgrade-package <name>`, then `uv sync`; `uv sync` also removes
what the lock does not list. Dependabot opens the upgrade PRs (`.github/dependabot.yml`), except for
the quality gate's tools.
`DATAFORGE_HOME` for dev is the clone (`export DATAFORGE_HOME=~/src/DataForge`), or the worktree
you work in. Never point it at the Windows folder.

### GetFinance workspace (df-bsb2.2)

`workspaces/GetFinance` is versioned in this repo and changes like any other file: in a DataForge
worktree, a branch and a PR. Its `README.md` says what its data is, how the demo and the real job
differ and how its tests work.

```bash
# From a worktree, with its venv and DATAFORGE_HOME set to the worktree:
dataforge run GetFinance finance_demo                 # the whole pipeline on synthetic data, no .env (~6 s)
dataforge run GetFinance finance_daily                # the real job: Pluggy fetch, needs a .env (prod)
pytest workspaces/GetFinance -m 'not integration'     # the workspace's pipeline tests, fake data (CI job tests-getfinance)
GETFINANCE_DATA=<win>/workspaces/GetFinance/data pytest workspaces/GetFinance -m integration   # real data, read-only
GETFINANCE_DATA=<win>/workspaces/GetFinance/data git commit -m '... (<issue>)'                 # the guard checks real values
```

The demo writes `workspaces/GetFinance/data/` of the checkout it runs in (gitignored); delete it to
start over. Its fetch refuses a `data/raw/` it did not write, and the real fetch refuses one the demo
wrote (`data/raw/SYNTHETIC`). The workspace's tests run in their own pytest process (its
`pytest.ini`; its `tests` package cannot share one with the engine's), so `pytest tests` does not
reach them. The app's unit tests and build run in CI job `app-financas`; its Playwright e2e is local,
after `finance_demo` (section "Workspace web apps" below).

One leak guard covers the whole repo, the workspace included: `scripts/leakguard.py` (pre-commit and
CI job `leak-scan`). It is the command; its parts are `scripts/leakguard_checkout.py`,
`leakguard_patterns.py`, `leakguard_values.py` and `leakguard_files.py`, each a mutation target in
`testloop.toml` with its tests in `tests/test_leakguard_<part>.py` (df-n8ac.36). It reads the real values from `GETFINANCE_DATA`, `.leakguard-workspaces` or the
main checkout's `workspaces/GetFinance/data/`, and fails outside CI when it finds none: then set
`GETFINANCE_DATA` on the command, never `LEAKGUARD_NO_REAL_DATA` on this machine (that waiver is for
someone with no real data at all). The workspace's own README, section "Leak guard", has the detail.

## Updating prod (Windows)

Prod is `.venv-prod` in the Windows folder, a non-editable install that the Task Scheduler runs,
plus the workspaces under `<win>/workspaces/`. `scripts/prod/deploy.sh` updates both, and the
post-merge hook runs it after `git pull --ff-only` on main in the main checkout (paths from
`~/.config/dataforge/prod.env`; read its header). It builds the wheel of HEAD, runs
`scripts/prod/update-prod.ps1` (refuses while a job runs, exit 3; keeps every release for
`-Rollback`), then `scripts/prod/mirror_workspace.py` copies `workspaces/GetFinance` of the same
commit over prod's (df-bsb2.2.6.2):

- it writes every file of the commit and deletes the files it wrote before that the commit dropped
  (`.mirrored-files` in the prod workspace lists them; without that file nothing is deleted);
- prod's own files are never written or deleted: `data/`, `.env`, `dataforge.db`, `report/`,
  `triggers/`, `deploy/.ledger/`, `.venv/`, caches, `config/investments.yaml`,
  `config/merchant_normalization.local.yaml`;
- while the prod workspace still has a `.git` (the old checkout of the private repo) it mirrors
  nothing and says so; the switch is df-bsb2.2.6.3;
- a failed mirror puts the workspace back and the deploy rolls the engine back too.

By hand, the engine only (the copy of update-prod in use lives in
`.venv-prod\dataforge-releases\tooling\`):

```bash
git fetch origin && rm -rf /tmp/df-rel && mkdir /tmp/df-rel && git archive origin/main | tar -x -C /tmp/df-rel
uv build --wheel --out-dir <c-tmp>/df-wheels /tmp/df-rel   # ~1 s; a folder on C: outside the DataForge folder
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File '<WIN>\.venv-prod\dataforge-releases\tooling\update-prod.ps1' \
  -VenvPath '<WIN>\.venv-prod' -Wheel '<C-TMP>\df-wheels\dataforge-0.1.0-py3-none-any.whl' \
  -Constraints '<WIN>\.venv-prod\dataforge-releases\tooling\constraints-win.txt' -DataForgeHome '<WIN>'
```

**Prod is busy (exit 3).** update-prod refuses while any process runs from `.venv-prod`. Usually
it is "DataForge em segundo plano" (`pythonw.exe -m dataforge background`, the API server and the
tray icon), which stays up after the app window closes until its grace period runs out. For a
deploy Lucas asked for, the session closes it itself (Lucas, 2026-11-21; df-u6st), never while a job
runs: (1) no `job_runs` row of the Windows `dataforge.db` is `running` or `pending`; (2)
`powershell.exe -NoProfile -Command "$env:DATAFORGE_HOME='<WIN>'; & '<WIN>\.venv-prod\Scripts\dataforge.exe' stop"`
(no `--force`: it answers `há job rodando` and stops nothing while a job runs); (3) an app or admin
window still open (`-m dataforge app ...`, `dataforge gui`, with its `QtWebEngineProcess.exe` children)
is closed with `Stop-Process -Id <pid>`; (4) the listing below shows nothing, and
`scripts/prod/deploy.sh` runs again. Outside a deploy a session does not stop it. App backends (`pythonw.exe -m
dataforge.app_backend_host backend.main:app <port>`) are its children and stop with it, however it
ends (df-xy7c). A `-m uvicorn backend.main:app` with a dead parent comes only from a release before
df-xy7c, whose server crashed (df-5lk8): those can be stopped by hand (`Stop-Process -Id <pid>`), nothing
else holds them. **The ninho server holds prod too** (df-8x0m.23.20.4, df-5dy3). While it runs from `.venv-prod`
(`python.exe -m uvicorn ninho.servidor.app:app --host 127.0.0.1 --port 8100`), update-prod refuses, and
`dataforge stop` does not know it. For a deploy Lucas asked for, the session stops it itself (Lucas,
2026-11-24: permanent; an `autoMode.allow` rule in `.claude/settings.local.json`): no job running,
`Stop-Process -Id <pid>` (no `-Force`), deploy, then start it again with `NINHO_WORKSPACE`,
`NINHO_BANCO` (`<WIN>\ninho-dados\ninho.db`; the people who log in are in it, df-8x0m.23.6.2). Then check from Windows, with nobody's password: `/` and `/icone-180.png` answer 200, `/api/eu` and one route of each module answer 401 with no `WWW-Authenticate` header, and `POST /api/entrar` with an invented name and password answers 401. The 200 of a data route needs a session (df-8x0m.23.31), so that one is Lucas's, on his phone.
To see who holds prod:
`powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | ? ExecutablePath -like '*DataForge\.venv-prod*' | Select ProcessId,ParentProcessId,CreationDate,CommandLine | Format-List"`.
A crash of the server shows in the Windows Application log (event 1000, `pythonw.exe`) and in
`<win>/logs/background.log` as a `Started server process` with no `Shutting down` before it.

(`<win>`/`<c-tmp>`: the Windows folder and a temp folder on C: as WSL sees them; `<WIN>`/`<C-TMP>`: the same as Windows paths.) `-Ref` also exists
but needs a checkout that Windows git can read, which the Windows folder stops being once its core
source is removed (df-qryy).

## Registering triggers with the OS scheduler

```powershell
# Windows, in an ELEVATED PowerShell (Run as administrator), with prod's own dataforge.exe:
$env:DATAFORGE_HOME='<WIN>'
& '<WIN>\.venv-prod\Scripts\dataforge.exe' register-triggers <ws>                 # all triggers of <ws>
& '<WIN>\.venv-prod\Scripts\dataforge.exe' register-triggers <ws> --trigger daily # only one
```

Re-registers each enabled trigger from its file (disabled ones are removed), with the command of
the `dataforge` that runs it and the S4U logon (runs whether or not you are logged on; no password).
Elevated, there is no prompt; non-elevated, Windows shows a UAC consent prompt per trigger, and a
declined prompt leaves the task "only when logged on" with a warning. Check afterwards:
`schtasks /Query /TN '\DataForge\<ws>\trigger\<name>' /XML` shows `<LogonType>S4U</LogonType>`
and the prod `dataforge.exe`. Rules and costs: `docs/architecture.md` "OS scheduler registration".

## Testing

```bash
# Run all tests with coverage. tests/conftest.py sets QT_QPA_PLATFORM=offscreen unless you set it (df-m4tz)
pytest tests/ -v --cov=dataforge --cov-report=term-missing

# Run a single file
pytest tests/test_cli.py -v

# Test order is random (pytest-randomly prints the seed); reproduce or switch it off:
pytest tests --randomly-seed=1234
pytest tests -p no:randomly

# Changed lines and branches no engine test runs, against origin/main (exit 1 if any); before a PR
python scripts/testloop/cli.py diff-cover [--compare-branch REF]
```

See `docs/testing.md`.

## Lint, dead code, import contracts, dependencies

```bash
python scripts/quality/gate.py check     # ruff, vulture, import contracts, deptry; fails only on findings above the baseline
```

See `docs/quality-gate.md`.

## CLI — from project root

```bash
source .venv/bin/activate
export DATAFORGE_HOME="$(pwd)"   # required: otherwise workspaces don't resolve

# Canonical pipeline run (top-level form):
dataforge run <workspace> <job>          # e.g. dataforge run GetFinance finance_demo
dataforge run <workspace> <job> --detach # for a program: prints one JSON object with the run id at once; the run goes on by itself
dataforge run-state <run_id>             # one JSON object: run_id, workspace, job, state, started_at, finished_at, failed_task, reason
dataforge run-state <run_id> --wait      # same object, answered only when the run has ended (one process follows the whole run)
dataforge run-state <run_id> --wait --timeout 30   # gives up after 30 s: answers the state so far, exit 4
#   state: pending | waiting (queued behind another job of the workspace) | running | success | failed
#   exit codes: 0 answered, 1 the job failed (run without --detach), 2 invalid request or unknown id, 3 reserved for "busy"

dataforge init <workspace_name>
dataforge validate --workspace <name>
dataforge jobs list --workspace <name>
dataforge jobs run <job_name> --workspace <name>
dataforge execucoes list --workspace <name>
dataforge execucoes logs <id> --workspace <name>
```

`execucoes logs <id>` prints the output (stdout and stderr) task row `<id>` stored when it ended,
under a `--- execução #<id> · <job>/<task> · <status> ---` header, as stored (not as rich markup).
Exit 1, with a message, when the id does not exist or the row has no output yet (a task still
running, or one that printed nothing). Like `execucoes list`, it reads `<ws>/data/dataforge.db`
(written by `jobs run`) unless `--db` names another; runs of `dataforge run`, `scheduled-run` and the
API are in `<ws>/dataforge.db` (df-ahua.16.14). Before df-ahua.16.2 it looked for a
`logs/execucao_<id>_*.log` file that nothing writes, and always failed.

## Cross-workspace deploy

A `dataforge deploy` command group exists for cross-workspace deploy (e.g. promoting a
workspace to prod). Run `dataforge deploy --help` for subcommands.

## CLI — from inside workspace dir (auto-detect)

```bash
cd workspaces/<name>
dataforge validate
dataforge jobs list
dataforge jobs run <job_name>
dataforge execucoes list
dataforge execucoes logs <id>
```

## API Server (Phase 1.2+)

```bash
# Start FastAPI dev server
uvicorn dataforge.api:app --reload --port 8000
```

```bash
# Start the server via the CLI (default 127.0.0.1:8000); also prints one URL per
# discovered workspace app, e.g. "Finanças: http://127.0.0.1:8000/apps/GetFinance/financas/"
dataforge serve [--host H] [--port P] [--reload]
```

`--host H` adds H to `DATAFORGE_ALLOWED_HOSTS` (the Host names the API accepts beyond
loopback; see `docs/subsystems/api.md`). `--host 0.0.0.0` or `::` sets it to `*`, warns that
the data is reachable on the network without login, and prints URLs with `127.0.0.1`.
`--port P` also sets `DATAFORGE_PORT=P` for the process (and the `--reload` child), so the app
backends it starts get `DATAFORGE_API=http://127.0.0.1:P` and every client it starts finds it.

```bash
# Open a workspace app in its own window (QWebEngine, no browser); starts the local server if needed
dataforge app <workspace> <app> [--home PATH] [--atalho]
```

`--home PATH` sets `DATAFORGE_HOME` before anything else. An unknown workspace or app shows an error
dialog (what a desktop-shortcut user sees: `pythonw` has no console), also prints the message when
there is a console, and exits 1 without starting the server.
`--atalho` creates a `.lnk` named `<title> (<workspace>).lnk` on the user's Desktop (the real folder,
from PowerShell `GetFolderPath('Desktop')`, so OneDrive redirection works) and does NOT open the window.
The shortcut runs `pythonw.exe -m dataforge app <ws> <app> --home <home>` with the home as working
directory, so double-clicking opens the window without a console. The workspace and app are validated
first (exit 1 if unknown). The icon is applied only if the app's `icon` is an `.ico`; otherwise the
default icon is used and the command prints a note. Outside Windows (including WSL) it prints the
open command and exits 0. The shortcut points at the interpreter that ran `--atalho` (recreate it
if the venv moves) and starts in the home folder, which `-m` puts on `sys.path[0]`.

### One local server, started on demand

`dataforge gui` and `dataforge app` are clients of a single local server on `127.0.0.1:<port>`
(`$DATAFORGE_PORT`, default 8000). Each calls `instance.ensure_server()`: it asks `/health` who owns the
port (service name, a fingerprint of the code, and the home), reuses the server when it is this
DataForge (same home, same code), and otherwise starts the background process detached (below) and
waits up to 20 s for it. Concurrent starts are serialized by a lock file (`<home>/locks/instance-start.lock`).
It refuses, with a message, when the port belongs to another program (set `DATAFORGE_PORT`), to a
DataForge of another home, or to a DataForge of another version (quit it from the tray, or
`dataforge stop`, and open again). A start that does not answer in time points at `<home>/logs/background.log`.

```bash
dataforge background    # hidden command (not in --help); you normally never run it by hand
```

`background` is the "DataForge em segundo plano" process: the API server, plus a tray icon when the
desktop has one (menu "Abrir DataForge", which launches `dataforge gui`, and "Sair", which asks first
if a job is running). It has no window of its own and its output goes to `<home>/logs/background.log`.
"Sair" confirmed with a job running: the server stops answering at once and the app backends are
stopped, but the process exits only when the task running at that moment finishes (its child process
is not killed); the job's later tasks do not start and its run is left unfinished.

**Auto-shutdown.** Every window (the admin and each app window) sends a presence heartbeat every
30 s (`HEARTBEAT_SECONDS`) and a goodbye when it closes; a window silent for 120 s (`EXPIRY_SECONDS`)
counts as closed, so a crash does not keep the server up. The background process checks every 10 s and
quits once no window is present and no job runs inside the server for 60 s (`GRACE_SECONDS`;
override with `DATAFORGE_GRACE_SECONDS`, in seconds; an invalid or negative value falls back to 60).
So it ends about 1 min after the last window closes. A server that no window ever connected to counts
from its start. App backends are stopped with it.

```bash
dataforge stop [--force]
```

Asks the running DataForge to shut down (`POST /api/instance/shutdown`) and waits up to 10 s for the
port to go quiet. Output and exit codes:

| Case | Output | Exit |
|------|--------|------|
| Stopped | `DataForge desligado` | 0 |
| Nothing on the port | `nenhum DataForge rodando` | 0 |
| Invalid `DATAFORGE_PORT`, port owned by another program or another home | the reason | 1 |
| Job running and no `--force` | `há job rodando` | 1 |
| Server started by `dataforge serve --reload`, or any server without a stop handler | `este servidor não pode ser parado por aqui; use Ctrl+C` | 1 |
| Could not reach it, or non-202 answer | `falha ao falar com o DataForge: ...` or the server's detail | 1 |
| Still answering after 10 s | `o DataForge ainda responde na porta <port>` | 1 |

A DataForge of another version but the same home is still stopped. `--force` stops it even with a job
running: the server stops answering at once, but the process does not exit at once. The background
process exits when the task running at that moment finishes (the job's later tasks do not start, as
with "Sair"); a plain `dataforge serve` waits for the whole job before it exits. `dataforge serve` (without `--reload`) registers the same stop handler, so `stop` also ends a
plain `serve`; it is still normally stopped with Ctrl+C, and with `--reload` Ctrl+C is the only way. `serve`
never shuts itself down: only the background process has the auto-shutdown.

## ninho house server (ADR 0006; walking skeleton df-8x0m.23.13)

The product's own server, in the `ninho/` package (it never imports `dataforge`; an import contract
in `pyproject.toml` guards both ways). It reads the published tables of a workspace straight from
the Delta files, read-only. Synthetic data only in dev: run the demo first.

**The whole skeleton with one command** (df-8x0m.23.13.6): copies the GetFinance workspace to a
scratch folder, runs `finance_demo` there and serves the routes and the screen over that copy; the
copy is removed when the server stops (Ctrl+C, SIGTERM, Ctrl+Break on Windows). Never the
checkout's own workspace. It sets `NINHO_BANCO` to `<the copy's folder>/ninho.db` (df-8x0m.23.20.3),
so the list starts empty and goes away with the copy, and creates there one person with every
module, the login the browser asks for: name and password from `NINHO_ESQUELETO_NOME` and
`NINHO_ESQUELETO_SENHA` (at least 8 characters; read only by the skeleton, never by the server).
Run again on the same `--home`, that person gets the password given and every module. Other
people, each with one module, are created when both of their variables are set:
`NINHO_ESQUELETO_NOME_LISTA` / `NINHO_ESQUELETO_SENHA_LISTA` (the list only) and
`NINHO_ESQUELETO_NOME_DESEJOS` / `NINHO_ESQUELETO_SENHA_DESEJOS` (the wishlist only; df-8x0m.23.23.2).

```bash
NINHO_ESQUELETO_NOME=<a name> NINHO_ESQUELETO_SENHA=<a password> python scripts/dev/esqueleto.py   # http://127.0.0.1:8100/
NINHO_ESQUELETO_NOME=<a name> NINHO_ESQUELETO_SENHA=<a password> python scripts/dev/esqueleto.py \
  --endereco <address> --porta <port> --home <folder>                  # the copy goes to <folder> and stays
```

The same command is what the end-to-end test (`pytest tests/ninho/test_esqueleto.py`: the server as
a process, read, correct, apply, read back, and a list item there and back, over HTTP, ~15 s) and
the screen's browser test start.

By hand, on a workspace of your own:

```bash
# From a worktree, with its venv and DATAFORGE_HOME set
dataforge run GetFinance finance_demo
export NINHO_BANCO=<a folder that exists>/ninho.db
python -m ninho pessoas criar <name> --modulos financas,lista          # asks the password twice
NINHO_WORKSPACE=$DATAFORGE_HOME/workspaces/GetFinance uvicorn ninho.servidor.app:app --host 127.0.0.1 --port 8100
curl -c ninho.cookies -H 'Content-Type: application/json' -d '{"nome": "<name>", "senha": "<password>"}' \
  http://127.0.0.1:8100/api/entrar                                    # 204 and the session cookie, kept in the file; 401 for a wrong login
curl -b ninho.cookies http://127.0.0.1:8100/api/eu                     # {"id", "nome", "modulos"}: who is logged in
curl -b ninho.cookies 'http://127.0.0.1:8100/api/financas/transacoes?mes=2026-03'
curl -b ninho.cookies http://127.0.0.1:8100/api/financas/edicoes     # corrections, categories, version, pendente
curl -b ninho.cookies -X PUT -H 'Content-Type: application/json' -d '{"subcategoria": "<one of the categories>"}' \
  http://127.0.0.1:8100/api/financas/correcoes/<transaction id>      # saved in the app database (data/edicao.db)
curl -b ninho.cookies -X POST http://127.0.0.1:8100/api/financas/aplicar                # "apply": the server asks the engine (`dataforge run ... --detach`); answers the run id
curl -b ninho.cookies 'http://127.0.0.1:8100/api/financas/aplicar/<run id>?esperar=60'   # the run's state; esperar holds the answer until it ends (up to 60 s)
```

- `NINHO_DATAFORGE`: the DataForge executable the server calls; default, the `dataforge` installed
  next to the server's interpreter. The server never imports `dataforge` and needs no DataForge
  server up. The engine's home is taken as two folders above `NINHO_WORKSPACE`.

- `NINHO_WORKSPACE`: the workspace folder; the server reads `<it>/data/curated/<table>`.
- `NINHO_BANCO`: the app database of ninho (`docs/architecture-v2/server-modules.md`), a SQLite file
  the server creates with each module's tables on first use; its folder must exist. It holds the
  people who log in, so unset, or its folder missing, every route answers 503, finances included
  (its edits stay in the workspace's `data/edicao.db`). Only the server and `python -m ninho
  pessoas` write to it. Read on every request, like the others here.
- **Login, per person, by session** (df-8x0m.23.6.2, df-8x0m.23.31;
  `docs/architecture-v2/people-and-visibility.md`, "The session"): `POST /api/entrar` with the JSON
  `{"nome", "senha"}` of a person of `pessoas` (below) answers 204 and the cookie `ninho_sessao`
  (`HttpOnly`, `Secure`, `SameSite=Strict`, 90 days); a wrong password, an unknown name and an empty
  field all answer the same 401. The screen shows its own login form; there is no HTTP Basic, and no
  answer carries `WWW-Authenticate`. Every other `/api/` route answers 401 without a live session.
  The cookie's token is random; the table `sessoes` of `NINHO_BANCO` keeps only its SHA-256, so a
  session survives a restart of the server. A session lives 90 days from its last use: a use more
  than a day after the last one pushes the expiry and sends the cookie again. `POST /api/sair` ends
  the session of the request (204, also without one). `python -m ninho pessoas senha <name>` ends
  every session of the person, and `python -m ninho pessoas sair <name>` does only that (a lost
  phone). Each module answers only to a person who has it, 403 to anyone else: `/api/financas/*`
  needs `financas`, `/api/lista/*` needs `lista`, `/api/desejos*` needs `desejos`; `/api/eu` and
  `/api/pessoas` need only a session. The files of the screen (`/`, its scripts and icons) are open
  to anyone: they hold no data. The password is checked only at `/api/entrar` (scrypt, ~200 ms); an
  empty name or password is 401 with no scrypt, an unknown name costs one, like a wrong password.
  Modules changed by the command count from the next request. A hash below today's cost is written
  again at login with the same password, only while the stored hash is still the one checked (never
  over a password changed meanwhile); a password under the 8-character minimum, or a database error
  there, leaves it as it is and the login still succeeds. With curl, the cookie file (`-c` at login,
  `-b` after) carries the session, on `127.0.0.1` too; the password goes in the body, so type the
  command with a leading space or read it from a prompt to keep it out of the shell history.
- `GET /api/eu`: `{"id", "nome", "modulos"}` of who is logged in, never the hash.
- `GET /api/pessoas`: `{"pessoas": [{"id", "nome"}]}`, everyone who may log in, in the order they
  were created; never the hash nor the modules. What a wish's `de_quem` points to.
- `GET /api/financas/transacoes?mes=AAAA-MM`: `{"mes", "transacoes": [...]}`, the rows of
  `fact_transaction_classified` whose `anomes_key` is that month, newest first, with the columns
  in `ninho/financas/leitura.py` (`COLUNAS`). 401 without a valid login, 403 to a person without `financas`, 422 for a bad `mes`, 503
  when the table does not exist yet (the pipeline has not run there).
- Tests: `pytest tests/ninho/test_leitura.py` (runs `finance_demo` on a scratch copy, ~10 s).

**The shopping list** (`ninho/lista`, df-8x0m.23.20.1): one list shared by everyone, in the table
`lista_itens` of `NINHO_BANCO`. Items are `{id, texto, comprado, adicionado_por, criado_em,
comprado_em}` (times in UTC ISO); `adicionado_por` is the name of the person logged in, as it is
stored. Every route needs a person with the module `lista`; 503 without `NINHO_BANCO`.

```bash
NINHO_BANCO=<a folder that exists>/ninho.db uvicorn ninho.servidor.app:app --port 8100
curl -b ninho.cookies http://127.0.0.1:8100/api/lista/itens          # {"itens": [...]}: to buy first (newest on top), then the bought ones
curl -b ninho.cookies -X POST -H 'Content-Type: application/json' -d '{"texto": "pão"}' \
  http://127.0.0.1:8100/api/lista/itens                                    # 201 with the item; empty or over 200 characters: 422
curl -b ninho.cookies -X PATCH -H 'Content-Type: application/json' -d '{"comprado": true}' \
  http://127.0.0.1:8100/api/lista/itens/<id>                               # the item; unknown id: 404
curl -b ninho.cookies -X DELETE http://127.0.0.1:8100/api/lista/itens/<id>         # 204; unknown id: 404
curl -b ninho.cookies -X POST http://127.0.0.1:8100/api/lista/limpar-comprados     # {"apagados": n}
```

- Tests: `pytest tests/ninho/test_lista.py` (~2 s). Who gets in to each route of finances, the list
  and the wishlist (two invented people, the finance calls stubbed) and `/api/eu`:
  `pytest tests/ninho/test_login.py` (~2 s). How a session opens, lives and ends:
  `pytest tests/ninho/test_sessao.py` (~2 s). In the tests `auth=(name, password)` on a request
  means "asked by this person": the client (`Cliente`, `tests/ninho/conftest.py`) logs in and sends
  the session's cookie. The tests share an app database with `ana`
  (every module) and `beto` (list and wishlist), at a cheap scrypt cost (`tests/ninho/conftest.py`).

**The wishlist** (`ninho/desejos`, df-8x0m.23.22.1): wishes in three parts, each person's and both's,
all seen and edited by everyone, in the tables `desejos_desejos` and `desejos_anotacoes` of
`NINHO_BANCO`. A wish is `{id, titulo, de_quem, adicionado_por, realizado, realizado_em, criado_em,
anotacoes: [{id, texto, link}], presente}`: `de_quem` is the id of a person (`/api/pessoas`) or `null` for
both (an empty text is `null` too; any other value is 422); `link` is true when the whole note is an
http or https address with a host (never `javascript:`, `ftp:` or text around it). Title up to 200
characters, note up to 2000, at most 50 notes per wish, no tab, line break or other control
character (422). Marking fulfilled keeps the first time; unmarking clears it. Deleting a wish deletes
its notes. Every route needs a person with the module `desejos`; 503 without `NINHO_BANCO`.

The surprise gift (df-8x0m.23.23.1): a person who is not the owner marks "vou dar" on a wish of one
person (table `desejos_presentes`, at most one mark per wish). Every wish answered carries `presente`:
always `null` for its owner and for a wish of both; for the others `null` or `{de: <name of who will
give it>, meu: <is it me>}`. The owner's own wish and a wish of both are 422 on both gift routes,
checked before the mark is read, so the owner gets the same answer marked or not. Moving the wish to
another part (`de_quem` changes) deletes the mark; deleting the wish deletes it; fulfilling does not.
Only who marked unmarks (anyone else not the owner: 409, as is marking a wish someone else marked).

```bash
curl -b ninho.cookies http://127.0.0.1:8100/api/desejos             # {"desejos": [...]}: not fulfilled first (newest on top), then fulfilled (most recent on top); notes in the order written
curl -b ninho.cookies -X POST -H 'Content-Type: application/json'   -d '{"titulo": "bicicleta", "de_quem": null, "anotacoes": ["https://exemplo.invalid/bici"]}'   http://127.0.0.1:8100/api/desejos                                       # 201 with the wish; anotacoes is optional
curl -b ninho.cookies -X PATCH -H 'Content-Type: application/json' -d '{"realizado": true}'   http://127.0.0.1:8100/api/desejos/<id>                                  # titulo?, de_quem? (moves it), realizado?: the wish; unknown id: 404
curl -b ninho.cookies -X DELETE http://127.0.0.1:8100/api/desejos/<id>                       # 204, with its notes
curl -b ninho.cookies -X POST -H 'Content-Type: application/json' -d '{"texto": "a cor azul"}'   http://127.0.0.1:8100/api/desejos/<id>/anotacoes                        # 201 with the wish, the note at the end
curl -b ninho.cookies -X DELETE http://127.0.0.1:8100/api/desejos/<id>/anotacoes/<anotacao_id>  # 204; a note of another wish: 404
curl -b ninho.cookies -X PUT http://127.0.0.1:8100/api/desejos/<id>/presente                 # 200 with the wish, presente {de, meu: true}; again: no change; own wish or both's: 422
curl -b ninho.cookies -X DELETE http://127.0.0.1:8100/api/desejos/<id>/presente              # 204, also with no mark; own wish or both's: 422; someone else's mark: 409
```

- Tests: `pytest tests/ninho/test_desejos.py` (~2 s).

**People** (`ninho/servidor/pessoas.py`, df-8x0m.23.6.1, df-8x0m.23.6.5; contract in
`docs/architecture-v2/people-and-visibility.md`): who may log in, in the table `pessoas` of
`NINHO_BANCO` (`id, nome, senha_hash, modulos, criada_em`), created on first use. A name is kept in
Unicode NFC without spaces at its ends; it is unique without telling upper from lower case (accents
included, and the same name typed in NFC or NFD is one person), has no `:`, no tab, line break or
other control character, and at most 50 characters. A password has at least 8 characters
(`SENHA_MINIMA`, Lucas 2026-11-24) and is compared in NFC. It is kept only as a scrypt
hash with its own salt (`scrypt$n$r$p$salt$key`, n=2^15, r=8, p=3: OWASP's minimum with 32 MiB per
check); a malformed stored hash is a wrong password, and `conferir` says `precisa_refazer` when a
hash is not at today's parameters. Modules today: `financas`, `lista`, `desejos`. People are created and
changed only by this command, run on the server machine with the server's venv, in a terminal: the
password is asked twice at the prompt, never taken as an argument nor read from a pipe:

```bash
python -m ninho pessoas criar <nome> --modulos financas,lista   # asks the password twice; name taken (any case): error
python -m ninho pessoas senha <nome>                             # asks the new password twice; the old one stops working and every session of the person ends
python -m ninho pessoas sair <nome>                              # ends every session of the person, on every device (a lost phone); the password stays
python -m ninho pessoas modulos <nome> lista                     # replaces the modules; 'nenhum' leaves none; unknown module: error
python -m ninho pessoas listar                                   # name, modules, created at (UTC); never the hash
```

- The database is `--banco <file>` (after the action; it wins over `NINHO_BANCO`) or `NINHO_BANCO`;
  its folder must exist.
- The name and the modules are checked before the password is asked; a short password is refused
  at the first typing. Errors in Portuguese on stderr, exit code 1 (2 for a wrong use of the
  command), never a traceback: the two passwords differing, Ctrl+C, the input ending at the prompt,
  an input that is not a terminal and a database that cannot be opened all change nothing.
- The server's login reads this table (df-8x0m.23.6.2, above).
- Tests: `pytest tests/ninho/test_pessoas.py` (~3 s; scrypt runs at a low cost there, one test at
  the real one).

### The ninho screen (`ninho/telas`, df-8x0m.23.13.5)

An iPhone-sized front (React + TypeScript + Vite + Tailwind, like the Finanças app). `src/App.tsx`
is the shell: a bottom bar switches between the modules, each with its screen in `src/<modulo>/`
(a new module is one entry in `MODULOS`); `?tela=<modulo>` opens one, else the first
(df-8x0m.23.20.2). The shell first asks `GET /api/eu` who is logged in: the bar shows, and the page
mounts, only the modules that person has (df-8x0m.23.6.3; the browser test logs in a second person,
list only, created by the skeleton from `NINHO_ESQUELETO_NOME_LISTA` / `NINHO_ESQUELETO_SENHA_LISTA`). The Finanças screen: the month's transactions, correct one category, "Há edições
não aplicadas", apply while following the run, reload. The server above hands out its built `dist/` at `/` to anyone logged in (the
browser asks once and sends the login again with every call; only the two icons, `icone-64.png` and
`icone-180.png`, open without it, because Safari fetches the home-screen icon without the login, df-8x0m.23.29); `?mes=AAAA-MM` opens a month, else today's.
Money comes as the route gives it (`amount_signed`, reais as a JSON number) and is rounded to
centavos only on screen.

The Compras screen (the module `lista`, shown as "Compras" in the bar and the title since df-8x0m.23.22.9; `src/lista/`, df-8x0m.23.20.3): a field with "Adicionar" on top (Enter also adds;
the field clears and keeps the focus), the items to buy, then the bought ones struck through, a tap
marks or unmarks one, ✕ deletes one, "Limpar comprados" shows only when something is bought; who
added an item shows under it when not empty. The shell keeps every screen mounted and passes each
`aberta`: the list reads `/api/lista` only when its screen is open, and again when it is reopened or
the page becomes visible again (the other person may have changed it). Each change goes to the
server and then the whole list is read again; marking and deleting show on the tap, and a tap
discards any read still in flight. Once the list is on screen, a re-read that fails keeps it and
shows a notice, which goes away at the next good re-read; only a first load that fails shows the
error with "Tentar de novo" (df-8x0m.23.20.7). Needs the
network: no local store, no queue (df-8x0m.23.8).

The Desejos screen (`src/desejos/`, df-8x0m.23.22.3), in the Lista's mould (same loading, empty,
error and re-read states, the same change-then-re-read): on top, the three parts, one button per
person of `/api/pessoas` and "Nós dois"; it opens on the last part used on this device (the
`localStorage` key `ninho.desejos.parte`; without storage it just forgets), else on the part of the
person logged in (`id` of `/api/eu`, which the shell now hands every screen as `eu`). Below, a field
"Novo desejo" adds to the part shown; each wish shows its title and its notes, and a note the server
marks `link: true` is an `<a target="_blank" rel="noopener noreferrer">` with the text as its href
(never HTML built from the text). A tap on the title opens the wish: add a note (one field, text or
address), ✕ deletes a note, change the title, "Mover para" another part, "Realizado" and "Apagar
desejo" (asks on the screen first). Fulfilled wishes sit in a collapsed "Realizados (n)" at the end
of each part, where they can be unmarked or deleted. A person without the module `desejos` does not
see it in the bar and the page never calls `/api/desejos` (browser test: `beto`).
The surprise gift (df-8x0m.23.23.2): an open wish of another person (not one's own, not "Nós dois")
shows "Vou dar este" (`PUT /api/desejos/{id}/presente`); once marked, the wish shows "Você vai dar"
under its title and the open wish "Não vou mais dar" (`DELETE`); a mark of someone else shows
"<name> vai dar" with no button. The screen only shows the `presente` the server sends (always null
for the owner) and has no optimistic mark; a refused mark (409, 422) shows the server's message like
any failed change. Browser test: `caio` (wishlist only) marks a wish of `ana` in a context of his
own, and every `/api/desejos` answer `ana`'s page gets has `presente` null on her wishes.

```bash
cd ninho/telas
npm ci
npm test           # unit tests (vitest)
npm run build      # type-check + build dist/ (served by the ninho server; commit it)
npm run test:e2e   # builds, then Playwright at iPhone size against a ninho server on a scratch copy
                   # of the workspace after finance_demo (scripts/dev/esqueleto.py), on a free port it
                   # picks itself, so two sessions can run it at once (`NINHO_E2E_PORTA=<port>` fixes one)
npm run dev        # Vite dev server; /api goes to a ninho server on 127.0.0.1:8100
```

- The browser test logs in as the skeleton's person (`ana`, set in `playwright.config.ts`) through
  `POST /api/entrar`, uses the
  worktree's `.venv/bin/python` (`NINHO_PYTHON` to change it) and Chromium
  with the iPhone 15 size, touch and user agent. `NAVEGADOR=webkit` runs it on WebKit, Safari's
  engine, which needs system libraries (`sudo npx playwright install-deps`) this machine lacks.
- CI job `telas-ninho` runs `npm test` and fails when the committed `dist/` differs from the build;
  the browser test stays local.

## Architecture drawings (C4, LikeC4; df-8x0m.23.33)

The model is versioned in `arquitetura/` (`hoje.c4` = what prod runs today, `alvo.c4` = the v2
target, `spec.c4` = element kinds and tags). Node runs it through `npx` with a fixed version; nothing
is installed in the repo. From the repo root (or a worktree):

```bash
npx likec4@1.61.1 start arquitetura        # live preview in the browser, reloads on save
npx likec4@1.61.1 validate --max-warnings 0 arquitetura   # syntax and semantics; a warning also fails, as in CI
npx likec4@1.61.1 build arquitetura --output-single-file -o /tmp/claude-1000/likec4-out   # one HTML
```

When prod changes shape, change `hoje.c4` in the same change. Each flow view of `fluxos.c4` links
the e2e test that exercises it, and each of its steps is an arrow of the model; renaming that test
means changing the link's title in the same PR, and a new step needs its arrow in `hoje.c4` or a
`componentes-*.c4`, or `python3 scripts/arquitetura/fluxos.py` (CI job `arquitetura`) fails
(`arquitetura/README.md`, "Fluxos").

Every element links its doc under `docs/` (`arquitetura/README.md`, "Doc de cada elemento"):

```bash
python3 scripts/arquitetura/docs.py esbocar    # a new element: writes its link in the .c4 and a sketch docs/caixas/<id>.md
python3 scripts/arquitetura/modelo.py gerar    # then regenerate modelo.json (esbocar reads it)
python3 scripts/arquitetura/docs.py conferir   # what CI runs: a missing link, doc or front matter fails, naming the element
```

## Workspace web apps (e.g. `workspaces/GetFinance/apps/financas`)

Run from the app folder; needs Node only for development:

```bash
npm run dev        # Vite dev server
npm test           # unit tests (vitest)
npm run test:e2e   # builds, then runs Playwright; run `dataforge run GetFinance finance_demo` first
npm run build      # type-check + build dist/ (served by DataForge; commit it)
```

The e2e refuses to start without `data/raw/SYNTHETIC` (the demo's data), so it never shows real
data. Before committing, run `npm run build` and commit `dist/` with every change to `src/`: CI job
`app-financas` fails when the committed `dist/` differs from the build.
