---
topic: Architecture — Layer Responsibilities
read_when: When you need a map of the codebase modules, key behaviors, and the cross-platform compat layer
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: []
code:
  - dataforge
status: pronto
---

# Architecture — Layer Responsibilities

> **Cross-module dependencies, hubs, and fan-in/fan-out** are not maintained by hand here; grep the
> code for them. The sections below are the human-authored **responsibilities**.

## Modules

- `cli.py` — Typer app. Subapps `jobs_app` and `execucoes_app`. Entry point `dataforge` via `pyproject.toml`. `--workspace` optional — auto-detects from `workspace.yaml` in cwd.
- `models.py` — `Task` (script XOR job, validated), `Job` (list of tasks + optional `env_file`), `Workspace`, `RetryConfig`, `Connection`, `Execucao`. `Task` validator raises if both or neither `script`/`job` set.
- `parser.py` — `load_workspace()`, `load_jobs()`, `load_job(workspace_dir, job_name)`, `load_connections()`. Tasks loaded inline from `job.yaml`.
- `dag.py` — `topological_levels(tasks)` — generic, works on any list with `name` + `depends_on`. Raises `ValueError` on cycles or unknown deps.
- `db.py` — `ExecucaoORM` with columns `job`, `task`, `status`, `inicio`, `fim`, `duracao_segundos`. `init_db(db_url)`.
- `engine.py` — `run_task()`, `run_task_with_retry()`, `run_job()`. `run_job` returns `dict[str, int | None]`.

## Key behaviors

- `run_task` is thread-safe: each call creates its own `Session`. Safe for `ThreadPoolExecutor`.
- Inactive tasks (`active: false`) excluded from execution; their deps are stripped from dependents before DAG resolution.
- Abort: if any task in a level fails after all retries, remaining tasks get `None` and never execute.
- `db_url` resolved via `workspace_path.resolve()` — always absolute path.
- `env_file` on `Job` is relative to workspace root; loaded by `_load_env_file()` in `engine.py`.

## OS scheduler registration (df-18s6, df-muzy)

`dataforge/cli.py` turns a trigger into an OS task (`_platform_register_trigger`; callers: the
triggers API, deploy reconcile, `dataforge register-triggers`). Rules:

- **Command:** the DataForge that registers the task (`_dataforge_command`): the `dataforge`
  console script next to the running interpreter (`sysconfig` scripts dir), else
  `<python> -m dataforge.cli`, as separate quoted tokens, with `--` before the names (a valid name
  may start with `-`). Not `shutil.which`: PATH order would pick the
  venv the task runs. Registering from another venv repoints the task at that venv, so register
  prod triggers with prod's `dataforge.exe`.
- **Windows logon: S4U** (`New-ScheduledTaskPrincipal -LogonType S4U -RunLevel Limited`, the
  current user). The task runs whether or not the user is logged on; no password is stored or
  asked. It runs in a non-interactive session: no console window (closing that window killed the
  2026-11-11 catch-up run with 0xC000013A). S4U has no network credentials (SMB shares, mapped
  drives, Windows-integrated auth) and no DPAPI/EFS (Credential Manager, `keyring`'s Windows
  backend, encrypted files). Plain HTTPS, `.env` files and local files under the user profile
  work. A job that needs those must not be scheduled this way.
- **Elevation:** Windows refuses S4U from a non-elevated process of an administrator under UAC
  (`0x80070005`, checked on Windows 11). Registration then shows the UAC consent prompt once; if
  declined or impossible it registers the task as interactive ("only when logged on") and returns
  a warning naming `dataforge register-triggers <ws> --trigger <name>` to run elevated.
- **WSL:** a task registered from WSL runs `wsl.exe`, which is not known to work in an S4U session:
  it stays interactive, with a warning.
- **Settings:** `-StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries` (df-413).
  Deliberately not set: `RestartCount/RestartInterval` (Task Scheduler does not restart a task
  whose action exits non-zero; checked on Windows 11 with a time-triggered task exiting 1),
  `RunOnlyIfNetworkAvailable` (a start without a network can leave the day with no run at all, and
  "a network" is not DNS working), `WakeToRun` (changes the machine's power behaviour).
- **Network not up yet (df-muzy):** retry inside DataForge, on the task that needs the network:
  `retry: {attempts: N, delay_seconds: S}` on that task in `job.yaml`. Only task-level `retry`
  takes effect (job-level `retry` is ignored today, df-a16k).

## Deploy ledger — crash-safety contract

`dataforge/deploy/ledger.py` keeps `<target>/deploy/.ledger/`: `objects/` (blobs named by their
sha256) and `deploylog.jsonl` (one `DeployEntry` per line). What holds when the process dies at any
write (checked by the crash sweep, `tests/test_crash_sweep_deploy.py`; df-ahua.5):

- **Atomic files.** Targets, blobs and every log rewrite go through `ledger.atomic_write` (temp file
  `.<name>.deploytmp` + `os.replace`): a path holds its old or its new content, never half.
  A blob that does not match its name (left by older code) fails `has_blob` and is rewritten.
- **Write-ahead apply.** `apply_plan` stores every blob the batch needs, appends the entry with
  `status: in_progress` listing all planned items, writes the targets, then rewrites the last line as
  `status: complete`. A write that raises leaves the entry with only the items written (D16); none
  written → no entry.
- **Unfinished entry** (the apply died): `rollback` undoes it as it stands (restoring the old content
  over a target never reached is a no-op); the next `apply` first records it as a complete entry
  holding only the items whose target no longer has the pre-deploy content (dropped if none), logs a
  warning, then deploys on top (LIFO rollback walks back through both). CLI `status`/`rollback` and
  the GUI provenance line add "not finished"; the API's entry payloads carry `status`.
- **Log rewrites** (`drop_last`, `replace_last`) swap a whole new file in, never truncate in place.
  A last line that does not parse (an append that died) is ignored with a warning and dropped by the
  next write; a bad line before the last still raises. Old entries without `status` read as complete.
- **Prune** ignores `.<name>.deploytmp` files (df-ahua.5.5); the write they belonged to replaces them.
- Not covered: power loss (no `fsync`), and a second apply running while one is in progress.

---

## dataforge.compat — Cross-Platform Path and URL Module

### Purpose

`dataforge/compat.py` is the single authority for all platform-sensitive path logic and SQLite
URL construction. It exists to fix two concrete Windows bugs:

1. **SQLite URL backslash corruption** — `f"sqlite:///{Path(base).resolve()}/..."` produces
   backslash-separated paths on Windows that SQLAlchemy cannot parse. `db_url()` fixes this via
   `path.resolve().as_posix()`, producing forward slashes on all platforms.
2. **`DATAFORGE_HOME` defaulting to `"."` (CWD)** — unpredictable when launching from a GUI
   shortcut. `data_dir()` uses `platformdirs.user_data_dir("DataForge", appauthor=False)` as the
   default, giving a stable platform-appropriate directory.

### Public API

```python
def data_dir() -> Path: ...
def db_url(path: Path) -> str: ...
def resolve_db(db: Optional[str], workspace_path: Path) -> str: ...
def resolve_workspace(name: Optional[str]) -> Path: ...
def list_workspaces() -> list[Path]: ...
```

- `data_dir()` — Returns `DATAFORGE_HOME` env var as Path if set; otherwise
  `platformdirs.user_data_dir("DataForge", appauthor=False)`. Creates directory on first call
  with a one-line stdout notice. Linux: `~/.local/share/DataForge`. Windows:
  `C:\Users\<user>\AppData\Local\DataForge`.
- `db_url(path)` — Returns `sqlite:///` URL with forward slashes via `path.resolve().as_posix()`.
  Windows-safe. Always pass the full path including filename (e.g. `db_url(data_dir() / "dataforge.db")`).
- `resolve_db(db, workspace_path)` — Returns `db` if given, else `db_url(workspace_path / "dataforge.db")`.
- `resolve_workspace(name)` — Returns `data_dir() / "workspaces" / name` if name given; otherwise
  checks CWD for `workspace.yaml`; raises `ValueError` if neither.
- `list_workspaces()` — Scans `data_dir() / "workspaces/"` and returns the workspace paths whose folder name is a valid workspace name (others are skipped with a warning).

### compat.py Rule

All path and database URL logic goes through `dataforge/compat.py`. No raw
`os.environ.get('DATAFORGE_HOME')` or inline `sqlite:///` f-strings elsewhere in the codebase.
Always use `from dataforge.compat import data_dir, db_url` for any path/URL construction.

---

## Manual Windows Checklist

Before any Windows-facing release, verify the following manually:

1. `pip install -e .` in cmd.exe creates `C:\Users\<user>\AppData\Local\DataForge\`
2. `dataforge gui` launches without SQLite errors
3. `dataforge run <ws> <job>` completes and writes DB
4. `dataforge validate` resolves workspace from `DATAFORGE_HOME`
5. API server starts and `/api/workspaces` returns correct workspace list

## Past learnings

<!-- LEARNINGS:AUTO -->
- 2026-05-19: DuckDB is opened `read_only=True` from all API endpoints; subprocesses own writes. <!-- obs:918 -->
- 2026-05-19: `run_job()` is always invoked via `asyncio.to_thread`, never directly in route handlers. <!-- obs:918 -->
- 2026-05-19: The FastAPI daemon runs embedded in the PySide6 app process (not standalone); panels talk to it over localhost HTTP. <!-- obs:918 -->
<!-- LEARNINGS:AUTO-END -->
