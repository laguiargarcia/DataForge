---
topic: Engine
read_when: "When working on DAG execution, task running, job orchestration, or the per-workspace run queue (and its WSL/Windows limit)"
subsystem: engine
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-762, df-b95, df-oe8, df-t7v]
code:
  - dataforge/conditions.py
  - dataforge/dag.py
  - dataforge/engine.py
  - dataforge/run_request.py
  - dataforge/runlock.py
status: pronto
---

# Engine

## API surface

<!-- AUTO:overview -->
<!-- AUTO-END:overview -->

## How to use

<!-- MANUAL:how-to-use -->
<!-- MANUAL-END:how-to-use -->

## Subsystem-specific rules

<!-- MANUAL:rules -->

### Per-edge conditions & skipped status

The engine evaluates each dependency edge independently and writes a
first-class `skipped` `ExecucaoORM` row when conditions are not met.
This replaces the older level-abort behaviour where a single failure
killed every downstream task as `None` (aborted).

**Edge grammar.** A `Task.depends_on` entry is either:

- a bare string (sugar for `{task: X, when: [succeeded]}`), or
- a `DependencyEdge` object: `{task: X, when: <cond> | [<cond>, ...]}`.

**Conditions** (values for `when:`):

- `succeeded` — parent ended `success` (the default).
- `failed`    — parent ended `failed`.
- `skipped`   — parent itself was skipped.
- `completed` — shorthand expanding to `[succeeded, failed]`.

A list inside `when:` is OR-semantic within that one edge:
`when: [failed, skipped]` means "run me if the parent ended failed
or skipped".

**Edge combination across multiple parents** is controlled by
`Task.match`:

- `match: all` (default) — every edge must be satisfied.
- `match: any`           — at least one edge must be satisfied.

When any edge is not satisfied (or `match` reduces to false), the
engine writes an `ExecucaoORM` row with `status="skipped"` (not
`None`). Inactive tasks (`active: false`) remain skipped silently
and still produce **no** `ExecucaoORM` row, as before.

### Outcome rule for `JobRunORM.status` (df-h864, replaces the leaf-only rule)

`engine.run_status(job, results)` decides the run; `run_job` records it and the CLI
(`run`, `jobs run`, `scheduled-run`) exits 1 exactly when it is `failed`. A run is `failed` when:

1. a task ended non-zero and **no dependent whose edge to it accepts `failed`** (`when: failed`,
   or `completed`) **ran** — nobody took the failure over; a failed leaf is the simplest case, or
2. a task whose handler declares `forces_run_failure = True` (the built-in `error` task type)
   ran and returned non-zero.

Otherwise it is `success`. Skipped tasks are neutral: they never fail the run themselves, but
they no longer hide a failure either. The old leaf-only rule (D10) recorded `success` when a
root failed and its whole chain was skipped, because every leaf was skipped (incident
2026-11-12, run a06e75d0: fetch failed 3x, 14 tasks skipped, `success`, exit 0).

What still counts as handled: a `when: failed` / `completed` dependent that actually ran (the
alert pattern: the handler ran, the run is `success`; if the handler itself fails and nobody
takes that over, the run is `failed`). What does not: a handler that was skipped (e.g. `match:
all` with another edge unmet) or inactive, and a `when: skipped` dependent further down the
chain. To force the run failed even when a handler ran, place an `error`-type task after it.
A `job`-type task returns 0 exactly when its sub-run's `run_status` is `success`.

### Run states and orphaned runs (df-bw7, df-ahua.5.6)

A run row (`job_runs`) is inserted `pending` by its caller (`run`, `scheduled-run`, the API's
`POST .../run`), set `running` by `run_job`, and finished `success`/`failed` by `run_job` in the same
process. A python task row (`execucoes`) is inserted `running` and finished after its script ends.
Every status change happens in the process that runs the job. If that process dies (kill, GUI
closed, power loss), no finalizer runs and the rows would say in progress forever.

So every row records its owner process (`owner_process`, see `db.md`), and
`dataforge/orphans.py:reconcile` fails the in-progress rows whose owner has **provably** ended
(`dataforge/liveness.py:owner_is_gone`). It never re-runs anything (df-413) and never touches a row
whose owner may be alive: a scheduled run or a terminal `dataforge run` can be in progress in
another process when the daemon starts.

The liveness rule. Gone means: same OS (`sys.platform`) and host as the process asking, and either
the Linux boot id differs (the kernel restarted), or, in the same pid namespace, no process has that
pid now (a Linux zombie counts as ended), or the process with that pid started at another time
(pid reuse). Never judged, so left as they are: rows of another OS or host (WSL and native Windows
can share a database; each side reconciles its own), of another Linux pid namespace (another WSL
distro, a container), rows with no owner (written before 2026-11-13; clear them by hand if needed,
see the `dataforge-zombie-running-root-cause` memory), and every row on platforms other than Linux
and Windows. The daemon's own in-process runs are alive while the daemon lives; if it dies, the next
daemon process reconciles them.

What an orphan becomes: `failed` (a status every surface already knows) with the reason
`orphans.REASON` ("interrupted: the process running it ended before it finished ..."). Run row:
`reason` set, `finished_at` = the last moment the run was seen alive (its start, or the latest
start/end of its task rows). Task row: `fim` = `inicio`, the reason appended to `output`,
`duracao_segundos` left unknown. A run killed after its last task succeeded but before the run row
was finished also ends `failed`: its outcome was never recorded.

When it runs: at daemon startup (lifespan, before the scan that publishes the runs still `running`),
before `GET /api/workspaces/{ws}/executions` (that workspace) and `GET /api/workspaces/` (all), and
before `dataforge execucoes list` (the store it lists). A run whose process died is therefore never
shown in progress by the GUI or the CLI, even while the daemon keeps running.

Not covered: a python task's script is a child process and may outlive a killed engine; its rows are
failed although the script may still be writing (df-bw7.1). In-process exceptions do not
need this: the CLI process ends with them (and is then reconciled), and the API's
`_run_job_background` fails the run itself.

### Per-workspace job queue (`dataforge/runlock.py`)

Jobs of one workspace run one at a time, whichever process starts them (the server, a scheduled
run, a terminal `dataforge run`). `run_job` wraps the whole run in `workspace_run_lock(workspace_dir,
run_id)`.

- **The lock** is an exclusive OS file lock (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) on
  `<home>/locks/<workspace>.lock` (`compat.run_lock_path`: the `locks/` folder of the home that holds the workspace, derived from the workspace path, not from `$DATAFORGE_HOME`). A waiter
  polls every 0.5 s.
- **Reentrant by `run_id`** within a process: a `type: job` task runs the nested job under the parent's
  `run_id` (on another thread), so it re-enters the lock it already holds (a depth counter per lock
  path) instead of deadlocking. A different `run_id` of the same workspace waits.
- **Released by the OS** if the process dies, so a crashed run never leaves the queue stuck. The lock
  file itself is left in place and is harmless.
- **Status while waiting.** The run row (`job_runs`) is inserted `pending` by its caller before
  `run_job` is called (the API, a trigger run, `dataforge run`), and `run_job` sets `running` only after
  it got the lock. A queued run therefore stays `pending`; the Jobs panel shows it as `aguardando`
  (`gui/panels/jobs.py`). The API publishes the `pending` event when it accepts the run. Exception:
  `dataforge jobs run` inserts no `job_runs` row (it runs under a synthesized `local-...` run_id), so a
  queued `jobs run` is not visible in the Jobs panel at all.
- **Telling the terminal.** `run_job(..., on_wait=...)` passes `on_wait` to `workspace_run_lock` /
  `file_lock`, which call it **once**, when the first non-blocking try finds the lock taken (never when
  it is free, never on reentry). `dataforge run` and `dataforge jobs run` pass a callback that prints
  `aguardando outro job do workspace terminar…`, so a CLI run that waits says so instead of hanging
  silently.
- **Limitation: a fila não atravessa WSL ↔ Windows.** The lock only works between processes of the
  same system (Windows with Windows, WSL with WSL). A lock taken by a WSL process is not seen by a
  Windows process, and the other way round, so a WSL run and a Windows run of the same workspace can
  overlap. This only matters when both sides use the same `DATAFORGE_HOME` (for example a home on
  `/mnt/c` opened from WSL and from native Windows). Keep the two from running the same workspace at
  the same time.

### Task type registry

`run_task` dispatches by `Task.type` via `dataforge/tasks/`:

- `python` (default when `script:` is set) — extracted subprocess runner.
- `job`    (default when `job:` is set)    — recursive `run_job`.
- `error`                                  — always returns 1, writes a
  `failed` `ExecucaoORM` row, flips the run-failed bit.

New types register via `dataforge.tasks.register(HandlerClass)` and
must conform to the `Handler` protocol (class-level `type_name`,
`required_fields`, `forces_run_failure`; instance `run(task, job,
workspace_dir, db_url, run_id) -> int`).

See spec at
`docs/superpowers/specs/2026-05-27-trigger-catchup-alerting-design.md`
(decisions D5, D6, D8, D9, D10, D11, D17).

<!-- MANUAL-END:rules -->

## Component diagram

<!-- AUTO:mermaid -->
<!-- AUTO-END:mermaid -->

## Past learnings

<!-- LEARNINGS:AUTO -->
- 2026-05-19: `run_job()` must run via `asyncio.to_thread`; never call it directly inside route handlers. <!-- obs:918 -->
- 2026-05-19: Hardcoded `sqlite:///dataforge.db` defaults were removed from engine functions (CR-01); DB paths come from `dataforge.compat`. <!-- obs:904 -->
<!-- LEARNINGS:AUTO-END -->
