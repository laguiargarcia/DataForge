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
