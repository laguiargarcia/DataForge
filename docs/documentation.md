---
topic: Documentation
read_when: When writing, naming, or maintaining files under docs/
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: []
---

# Documentation

## Structure

All project docs live in `docs/`. Filenames are the index — Claude decides relevance from name alone, so be descriptive.

| File | Contains |
|------|---------|
| `docs/architecture.md` | Layer responsibilities, data flow, execution stack |
| `docs/commands.md` | Build, test, lint, CLI, API server commands |
| `docs/testing.md` | Test setup, patterns, invariants, coverage requirements |
| `docs/workspace-layout.md` | Disk layout, YAML reference for workspace/job/task definitions |
| `docs/scripts-conventions.md` | Script patterns, param conventions, logging |
| `docs/gsd-workflow.md` | GSD phase workflow, planning artifact map, architecture constraints (main checkout only, not versioned) |
| `docs/documentation.md` | This file — how docs are written and maintained |

## Decisions

A decision is a bead of type `decision`, not a doc (Lucas, 2026-11-27; df-883u). The bead's
description says what was decided and why, with the options weighed; its notes record that Lucas
read and accepted it. Whatever the decision changes is written into the reference doc of that part
in the same change, so the doc says the rule and the bead says why. `docs/adr/` is frozen history:
no new ADRs, no edits.

## Front matter (df-8x0m.23.39.1)

Each element of the C4 model (`arquitetura/*.c4`) links one doc here, and that doc opens with a
front matter. `python3 scripts/arquitetura/docs.py conferir` (CI job `arquitetura`) fails when one
is missing or incomplete:

```yaml
---
topic: Engine
read_when: When working on DAG execution, task running, or job orchestration
code:
  - dataforge/engine.py
  - dataforge/dag.py
status: pronto
---
```

- `topic` and `read_when`: what the doc is about and when to read it.
- `code`: the files and folders the doc covers, from the repository root. It has to cover the
  `metadata.codigo` of every element that links the doc (a folder covers what is under it); the
  doc may add paths, and each one must exist. The model is the single source: change `codigo` in
  the `.c4`, then here.
- `status`: `esboço` (a sketch) or `pronto` (a real doc). A box without a doc gets a sketch in
  `docs/caixas/` from `python3 scripts/arquitetura/docs.py esbocar`, built from the model alone;
  writing the real doc over it ends with `status: pronto`.
- Other keys (`related_issues`, `last_synced_at`, `subsystem`) may stay.

The block is a flat subset of YAML, read without a YAML library (the bead hook runs `python3 -S`):
only `key: value` lines (plain, `"double-quoted"`, `'single-quoted'` or `[a, b]`) and `key:`
followed by `- item` lines; nothing nested. Quote a value that holds `: ` or starts with a YAML
sign, so GitHub still renders it.

## How to Write Docs

- Lead with a `#` heading matching the filename topic
- Use tables for reference material, code blocks for commands/examples
- No invented content — only extract from working code or planning artifacts
- Keep each file focused on one topic; cross-reference with links to other docs/

## How to Update Docs

- Update `docs/gsd-workflow.md` when phase status changes (main checkout only, not versioned)
- Update `docs/architecture.md` when a new architectural constraint is locked
- Update `docs/commands.md` when new CLI subcommands or API endpoints are added
- After migrating content from CLAUDE.md, verify the source was removed

## What Stays in CLAUDE.md

Only content that must be present in EVERY session regardless of task:
- Workflow (how to use docs/)
- GSD Planning (how to read planning artifacts)
- Beads tracker and session completion protocol
