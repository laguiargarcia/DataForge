---
topic: DB
read_when: "When changing schema or queries (SQLAlchemy ORM over SQLite: the execucoes, job_runs and schedules tables of dataforge.db)"
subsystem: db
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: []
code:
  - dataforge/db.py
  - dataforge/liveness.py
  - dataforge/orphans.py
status: pronto
---

# DB

## API surface

<!-- AUTO:overview -->
<!-- AUTO-END:overview -->

## How to use

<!-- MANUAL:how-to-use -->
`dataforge/db.py` is **SQLAlchemy ORM over SQLite** (`DeclarativeBase`). Three tables:

- `ExecucaoORM` → `execucoes` — per-task execution rows
  (`job`, `task`, `status`, `inicio`/`fim`, `duracao_segundos`, `output`, `run_id`, `owner_id`,
  `owner_process`).
- `JobRunORM` → `job_runs` — one row per job run, keyed by string `id`
  (`workspace`, `job`, `status`, `started_at`/`finished_at`, `owner_id`, `trigger`, `owner_process`,
  `reason`; `trigger` is `"manual"` for CLI/GUI runs or the trigger name for OS-scheduled runs;
  `reason` says why a run ended when the status alone does not, today only an interrupted run).
- `owner_process` (both tables) is the process that wrote the row, as JSON
  (`dataforge.liveness.Owner`: OS, host, Linux pid namespace and boot id, pid, process start time).
  It is the column default, so every insert records it; rows from before 2026-11-13 have NULL.
  It is not `owner_id`, which is the (future) user and is always `'system'` today.
- `ScheduleORM` → `schedules` — id `"{workspace}:{job}"`, `cron`, `enabled`,
  `last_run_at`/`next_run_at`, `owner_id`.

`init_db(db_url)` calls `Base.metadata.create_all(...)` then runs an **additive migration**:
because SQLite lacks `ADD COLUMN IF NOT EXISTS`, it reads `PRAGMA table_info(<table>)` and
issues `ALTER TABLE ... ADD COLUMN` only for missing columns, listed in `_ADDED_COLUMNS`
(currently `execucoes.owner_id`, `.output`, `.run_id`, `.owner_process`; `job_runs.trigger`,
`.owner_process`, `.reason`). It returns the engine.

Every caller makes its own engine and none disposes of it, so the engine of a database **file**
uses `NullPool` (df-n8ac.19): a connection closes when its session or `with` block ends, and nothing
stays open holding the file. An in-memory URL keeps SQLAlchemy's default pool (the database is the
connection). Do not add `engine.dispose()` after a session, and close a raw `sqlite3` connection
with `contextlib.closing` (sqlite3's own `with` only ends the transaction).

**Where the rows live.** `job_runs` is in the global `<DATAFORGE_HOME>/dataforge.db`. `execucoes` has
two stores per workspace: `<ws>/dataforge.db` (written by `run`, `scheduled-run` and the API; read
by the GUI and `/api/executions/...`) and `<ws>/data/dataforge.db` (written by `jobs run`, read by
`dataforge execucoes list` and `execucoes logs`; df-ahua.16.14). A task's stdout and stderr go to
`execucoes.output` once, when it ends; the column has no size limit.

**Orphaned rows (df-bw7).** A run whose process dies leaves `pending`/`running` rows. They are
corrected by `dataforge/orphans.py` (see `engine.md`, "Run states and orphaned runs").
<!-- MANUAL-END:how-to-use -->

## Subsystem-specific rules

<!-- MANUAL:rules -->
- This module is **pure SQLAlchemy/SQLite** — there is no DuckDB read tier here. DuckDB/Delta
  live only in the workspace ETL layer, not in `db.py`.
- New columns must be added both to the ORM class **and** to `_ADDED_COLUMNS` (the `init_db`
  PRAGMA-guarded `ALTER TABLE` list), or existing databases won't pick them up; then add them to
  `_MIGRATED` in `tests/test_db_properties.py`.
<!-- MANUAL-END:rules -->

## Component diagram

<!-- AUTO:mermaid -->
<!-- AUTO-END:mermaid -->

## Past learnings

<!-- LEARNINGS:AUTO -->
- 2026-05-19: Every SQLite table carries `owner_id TEXT NOT NULL DEFAULT 'system'`; auth becomes additive in Phase 4.1. <!-- obs:918 -->
- 2026-05-19: SQLite URLs are built only via `compat.db_url()` (`Path.resolve().as_posix()`); no inline `sqlite:///` f-strings. <!-- obs:907 -->
<!-- LEARNINGS:AUTO-END -->
