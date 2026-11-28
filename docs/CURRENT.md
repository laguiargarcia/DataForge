---
topic: Current
read_when: Quick orientation on what phase/plans/issues are active right now.
last_synced_at: "2026-05-23T04:28:53Z"
---

# Current

<!-- AUTO:gsd-current -->
## Active phase

**Phase:** 2.1 — SQL Workbench
**Status:** Ready — not started
**Current plan:** Not started

## In-progress plans

_None — Phase 2.1 has not been planned yet._

## Key decisions (recent)

- 80% coverage threshold via `[tool.coverage.report] fail_under` (not addopts) so omit list is respected before threshold evaluation
- `dataforge/cli.py` omitted from coverage threshold — CLI commands require real workspaces, unit testing fragile
- SSE via `StreamingResponse + text/event-stream` (not `EventSourceResponse`) — new `Session` per polling tick mandatory
- UI framework: PySide6 (Qt) — native look, rich widgets, Python-native, no extra runtimes
- Scheduler: OS-level cross-platform — Task Scheduler (Windows), launchd (macOS), cron (Linux); jobs fire when app is closed
- Authentication: optional toggle in Settings, disabled by default
- compat.py rule: all path/URL logic through `dataforge.compat`; no raw `DATAFORGE_HOME` reads or inline `sqlite:///` f-strings
- 01.5-06 Task 2: GUI reuses `GET /api/workspaces/` and plucks `name` — no new `/names` endpoint (API surface conservative)

## Open blockers

_None._

_Manual snapshot (the `/sync` GSD layer and `.planning/` dir are retired). The phase/plan
content above is hand-maintained and may be stale — `bd ready` / `bd list` is the live
source for open issues._
<!-- AUTO-END:gsd-current -->

<!-- AUTO:bd-open -->
| ID | Title | Priority | Status |
| --- | --- | --- | --- |
| df-0lf | GUI Jobs pane shows no live 'running' status for trigger-launched jobs | P2 | ready |
| df-37a | API/GUI registers OS scheduled task for disabled triggers | P2 | ready |
| df-3ul | WSL trigger registration: dataforge fallback quoted as a single bash token | P2 | ready |
| df-762 | Trigger-time validation that supplied param values match declared JobParameter.type | P2 | ready |
| df-gt9 | API + GUI: surface 'skipped' ExecucaoORM status | P2 | ready |
| df-ibb | GetFinance workbook: remove in-workbook M-code calendar, consume dim_calendar.parquet | P2 | ready |
| df-t7v | Engine substitution of ${params.X} into Task.params at runtime | P2 | ready |
| df-wub | Deployment Pipeline (cross-workspace deploy) | P2 | ready |
| df-coq | Apply global-budget change to prod workbook Relatorio_Financeiro.xlsx | P3 | ready |
| df-e93 | Retire stale _cc_dev_only test markers (cc/fatura columns now in prod) | P3 | ready |
| df-0o7 | MainWindow: couple SECTIONS to panel modules (remove parallel-list footgun) | P3 | open |
| df-3c6 | Spec A polish: type[Handler] annotation + unknown-dep regression test + shared envfile util | P3 | open |
| df-3qd | scheduled-run exits silently on disabled trigger — no log, indistinguishable from success | P3 | open |
| df-6w0 | Spec B polish: Twilio missing-env test specificity + E2E ExecucaoORM row assertions | P3 | open |
| df-84x | GetFinance docs: stale individual-script paths + transactions.xlsx PQ refs (post-reorg/post-parquet drift) | P3 | open |
| df-8e6 | Power Query: filter redeemed (current_value=0 / TOTAL_WITHDRAWAL) holdings from investments view | P3 | open |
| df-96o | Deploy panel: minor UI robustness (dup-path display dedup + stale-reply guard) | P3 | open |
| df-b95 | SecureString / secrets handling in trigger params | P3 | open |
| df-bii | GetFinance: add 429/Retry-After backoff to Pluggy v2 transactions cursor fetch | P3 | open |
| df-cri | Curated dim_calendar orphaned after fact_balance_monthly drop | P3 | open |
| df-oe8 | Dynamic content expressions in trigger params (@utcnow, etc.) | P3 | open |
| df-rir | GetFinance: derive holding identity from natural key (isin / issuerCNPJ+dueDate), retire per-holding investments.yaml | P3 | open |
| df-wz0 | Move credit-card panel from Resumo to dedicated Cartão sheet | P3 | open |
<!-- AUTO-END:bd-open -->
