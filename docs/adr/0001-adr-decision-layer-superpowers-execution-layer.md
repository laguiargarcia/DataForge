# Decision records live in ADRs; superpowers plans stay for execution

**Status:** accepted (2026-06-22)

The repo carried three overlapping planning systems — GSD (`.planning/`), superpowers
specs+plans (`docs/superpowers/`), and beads. We collapsed the *design-doc* layer to one:
`docs/adr/` + a lazily-created root `CONTEXT.md`, authored via the **grill-with-docs** skill,
become the home for *why*-decisions. GSD is retired; superpowers *specs* are frozen in place.

Superpowers `writing-plans` / `executing-plans` are **kept** for step-by-step execution
choreography — an ADR records *why* a thing is done, not the *how* of doing it. Beads stays as
the task tracker (an orthogonal axis: *what / status*, not *why*).

## Considered options

- **(A) ADRs as the decision-record layer only; keep superpowers plans for execution.** Chosen.
- **(B) Drive execution straight off ADRs + beads issues, drop superpowers plans entirely.**
  Rejected — superpowers plans carry executable step choreography (e.g. the 104 KB GUI-editor
  plan) that ADRs deliberately don't capture.

## Consequences

- In-flight superpowers plans finish in place on their existing files: `df-e9r` (GUI job/task
  editor), `df-dgu` (Report v2 epic), and the clean-repo migration. Only *new* design specs go
  to ADRs. The 18 frozen specs are **not** backfilled.
- ADRs and `CONTEXT.md` are gitignored / local-only (inherits the locked **D9** decision that
  tests & docs stay private, never committed to the public Show repo).
- `CONTEXT.md` is seeded lazily — only when a real product-domain term needs recording, not for
  tooling/process vocabulary.
