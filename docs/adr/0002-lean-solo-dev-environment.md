# Lean solo dev-environment: minimum MCPs and SessionStart hooks

**Status:** accepted (2026-06-22)

> **Amended 2026-11-16 (df-1nq2):** the `bd memories` → `MEMORY.md` projection is gone. The
> `gen_memory_projection` hook no longer exists and the harness memory directory is kept empty;
> `bd memories` is read directly. `bd prime` is still on PreCompact (user settings). claude-mem, GSD
> and `/sync` were uninstalled on 2026-11-03, not just left unused. The rest of this ADR stands.

Startup carried heavy overhead for a 156-file repo the solo dev knows well. We keep only what
earns its keep on a small, well-understood codebase and cut the rest. Everything cut lives in the
dev environment (parent `.claude/`, user-global hooks, gitignored `.planning/`) — none of it
changes what the public repo ships.

**Keep:** beads (tracker + memory via `bd memories` → `MEMORY.md`); context-mode (large-output
routing only, e.g. Excel COM dumps); excel-com (GetFinance workbook automation).

**Drop:**
- **code-review-graph** — impact/caller value is marginal versus grep on a small repo you know,
  and its PostToolUse `update` hook taxed *every* Edit/Write/Bash.
- **claude-mem** — a redundant third memory layer alongside beads + `MEMORY.md`; its session
  timeline was injected unread each startup.
- **GSD** (`.planning/`) — dead since 2026-05-20; its ~60 skills + ~30 agents bloated the surface.
- the **/sync** apparatus — its reason to exist evaporated once graph, GSD, and claude-mem were
  dropped (three of its six reconciled sources gone).

**Hooks:** keep only `gen_memory_projection` (refreshes `MEMORY.md` from beads). Drop `bd prime`
(a 98.6 KB SessionStart dump), `code-review-graph status`/`update`, `sync-status-check`, and the
cross-OS `claude-sync` hook.

## Consequences

- The project `CLAUDE.md` "always use code-review-graph before grep/Read" mandate is removed.
- claude-mem / GSD / `/sync` skills stay installed-but-unused (zero runtime cost) unless later
  uninstalled to shorten the skill list.
- The `sync-*` hooks are **user-global**, so dropping them affects all projects, not just
  DataForge — accepted.
- `bd prime` survives on the `PreCompact` hook only (useful after compaction; not every session).
