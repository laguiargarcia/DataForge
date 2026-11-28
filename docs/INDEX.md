# INDEX

Routing manifest. Sorted alphabetically by file path. Hand-maintained — edit
this table directly when adding or moving docs.

docs/ is versioned (df-azm6) and edited in a worktree like any file. Its history
(`docs/superpowers/`, `docs/phases/`, `docs/gsd-workflow.md`) and `docs/workbook-handoff.md`
hold real values: they stay gitignored and exist only in the main checkout.

| File | Topic | Read when |
| --- | --- | --- |
| docs/CURRENT.md | Current | Quick orientation on what phase/plans/issues are active right now. |
| docs/adr/0001-adr-decision-layer-superpowers-execution-layer.md | History (frozen ADR) | Background only: how the ADR layer was adopted in 2026-06 (frozen by df-883u) |
| docs/adr/0002-lean-solo-dev-environment.md | History (frozen ADR) | Background only: why graph tools, GSD, claude-mem and /sync were retired |
| docs/adr/0004-automated-test-system.md | Frozen ADR, still in force until df-cyjt | When designing or building automated tests (generators, mutation, branch map, test selection, data checks) |
| docs/adr/0005-dev-on-linux-disk-windows-is-prod.md | Frozen ADR, still in force until df-cyjt | Where to work (clone on ext4 vs the Windows prod folder), making a worktree with its venv, updating prod, migrating a session, the beads database flip |
| docs/adr/0006-architecture-v2-four-pieces.md | Frozen ADR, still in force until df-cyjt | When you need the target architecture: the four pieces (house server ninho, app database, lake, DataForge as orchestrator only), who owns each piece of data, which way dependencies point, the repository layout with walls |
| docs/architecture-v2/README.md | Architecture v2 contracts | Building or changing anything that crosses a boundary between the house server, the app database, the lake and DataForge; the list of contracts in `docs/architecture-v2/`, their format and what is still undecided |
| docs/architecture.md | Architecture — Layer Responsibilities | When you need module responsibilities, key behaviors, and the cross-platform compat layer (cross-module deps/hubs → grep the code) |
| docs/bead-check.md | Bead check (claim and close conditions) | A "Conferência do bead" refusal on `bd update --claim` or `bd close`; which labels a close needs, their values, the cutoff, the escape hatch; a "Subagente sem bead" refusal (participants hook) |
| docs/commands.md | Commands | When invoking project CLI / scripts, running tests, or the API server; running the GetFinance workspace's demo (`finance_demo`) or its tests; opening a workspace app (`dataforge app`, `--atalho`), `dataforge stop`, the background process and its auto-shutdown |
| docs/conventions.md | Project-wide conventions (graph-first exploration, audience principle) | Starting a new task that touches code; when deciding how to explore the codebase; when writing Obsidian-facing notes |
| docs/documentation.md | Documentation | When writing, naming, or maintaining files under docs/ |
| docs/getfinance-pluggy-integration.md | GetFinance / Pluggy finance pipeline | When working on the GetFinance ETL or Pluggy integration |
| docs/gsd-workflow.md | GSD Workflow | When orienting on planning artifacts, phase/plan structure, or session-close protocol |
| docs/phases/01.5-scheduler.md | Phase 01.5: scheduler | Understanding what was decided/built in phase 01.5-scheduler |
| docs/phases/1.2-orchestration-api.md | Phase 1.2: orchestration-api | Understanding what was decided/built in phase 1.2-orchestration-api |
| docs/phases/1.3-desktop-shell.md | Phase 1.3: desktop-shell | Understanding what was decided/built in phase 1.3-desktop-shell |
| docs/phases/1.4-job-monitoring-ui.md | Phase 1.4: job-monitoring-ui | Understanding what was decided/built in phase 1.4-job-monitoring-ui |
| docs/phases/cross-platform-compatibility.md | Phase: cross-platform-compatibility | Understanding what was decided/built in phase cross-platform-compatibility |
| docs/phases/knowledge-sync.md | Phase: knowledge-sync | Understanding what was decided/built in phase knowledge-sync |
| docs/quality-gate.md | Quality gate | When a commit, CI run or Claude Code hook fails with "quality gate"; adding or tuning a lint rule; suppressing a finding |
| docs/qa-environment.md | QA environment (DataForge-QA) | When working on QA/validation of the GetFinance report workbook (workbook era, not maintained since df-cns), or you need the sibling DataForge-QA folder's location, purpose, workbook, and keep-clean rule |
| docs/scripts-conventions.md | Workspace Scripts Conventions | When writing workspace-level scripts (functions.py, argparse params, logging) |
| docs/session-coordination.md | Session coordination (hooks, guard, board, session beads) | Several sessions at once; "whose worktree is this?"; a "Session guard" warning; activating or debugging the session hooks |
| docs/subsystems/api.md | API | When working on the HTTP / FastAPI surface (routers, the app backend proxy, `/health`, `/api/instance` presence and shutdown) |
| docs/subsystems/compat.md | compat.py path and database URL discipline | Touching anything that constructs filesystem paths, reads DATAFORGE_HOME, or builds a SQLite/database URL |
| docs/subsystems/db.md | DB | When changing schema or queries (SQLite execucao DB, DuckDB read tier) |
| docs/subsystems/engine.md | Engine | When working on DAG execution, task running, job orchestration, or the per-workspace run queue (and its WSL/Windows limit) |
| docs/subsystems/gui.md | GUI | When working on the PySide6 / Qt desktop surface |
| docs/subsystems/tasks-message.md | Message task | When configuring an alert, notification, or any `type: message` task |
| docs/testing.md | Testing Invariants | When writing or debugging tests |
| docs/workspace-layout.md | Workspace Layout & YAML Reference | When dealing with workspace paths, per-workspace data, or YAML reference for workspace/job/task definitions; writing a workspace web app and its backend (`app.yaml`, env, lifetime, icon) |
