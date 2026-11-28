---
topic: Project-wide conventions (codebase exploration, audience principle)
read_when: Starting a new task that touches code; when deciding how to explore the codebase; when writing Obsidian-facing notes
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: []
transversal: true
---

# Conventions

Project-wide conventions that apply across all subsystems.

## Codebase exploration

Explore the codebase with Grep/Glob/Read directly. This is a small, well-understood codebase; there is no knowledge graph (the graph-first mandate was retired — see `docs/adr/0002`).

## Audience principle (Obsidian-facing notes)

Obsidian notes are distilled views for a human reader, not a dump of `docs/`. Write each note for the audience that will read it (operator, planner, reviewer); keep canonical detail in `docs/` and reference it from the note rather than duplicating.
