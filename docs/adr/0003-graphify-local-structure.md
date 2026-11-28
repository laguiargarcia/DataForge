# Local graphify structure report at SessionStart

**Status:** superseded (2026-11-03): graphify removed in the plugin cleanup; grep is the only exploration tool. Accepted 2026-07-03.

ADR 0002 dropped the code-review-graph MCP: on a small, known repo its impact/caller value was
marginal versus grep, and its `PostToolUse update` hook taxed *every* Edit/Write/Bash. That
reasoning still holds for **live, per-edit** graphing and for **point lookups**. It does **not**
hold for one thing 0002 left to hand-authored docs: the **structural / orientation view**
(`docs/code-map.md` cross-module deps, module hubs, core abstractions). Those docs drift, because
keeping them current depends on the agent remembering to edit them — and the agent forgets.

We re-adopt a graph for that view only, in a leaner mode that avoids every cost 0002 objected to.

**Decision.** A local [graphify](https://github.com/safishamsi/graphify) run produces
`dataforge/graphify-out/GRAPH_REPORT.md` (god nodes, Leiden community map, EXTRACTED/INFERRED
cross-file edges), refreshed by a **SessionStart** hook. Agents read it first for structure; grep
stays the tool for live lookups and anything read/edited.

**Why this clears 0002's objections:**
- **Not per-edit.** The rebuild fires once per session, not on every Edit/Write/Bash — no per-tool tax.
- **Zero tokens.** Code-only `graphify update` is pure Tree-sitter (Pass 1); no LLM, no API calls.
- **Can't drift.** Derived from the code each session, so it never lags the way a hand doc does.
- **Non-blocking.** On this `/mnt/c` (9p) mount the rebuild costs ~18s (small-file I/O, not import
  or graph work — verified: native `/tmp` is ~1.4s, the penalty is the mount). The hook runs
  **detached** (`nohup … &`), so session start is instant and the report refreshes ~18s in; an
  early orientation query just reads the prior session's report.

**Scope — local only, never shipped** (like beads, `pytest.ini`, the old graph):
- venv lives **outside the repo** at `~/.graphify/venv` (native FS, not `/mnt/c`).
- `.graphify/` and `graphify-out/` are gitignored; the hook lives in `.claude/settings.local.json`.
- Install strictly from the source repo: `pip install "git+https://github.com/safishamsi/graphify"`.
  **Not** the PyPI package `graphifyy` — it is an anonymous upload (empty author/homepage) whose
  name differs from the repo; that is a typosquat signature and Claude Code's classifier blocks it.

## Consequences

- The `code-review-graph` MCP is de-enabled in `.claude/settings.local.json` (`enabledMcpjsonServers:
  []`) — it was the retired tool graphify now supersedes, and it was loading ~40 tools per session.
- `docs/code-map.md` was **deleted** (2026-07-03) — the derived report supersedes its cross-module-deps
  content. `docs/architecture.md` now points dependency/hub questions at the report and keeps only the
  human-authored module responsibilities; `docs/INDEX.md` drops the code-map row.
- The report is confirmatory, not revelatory, on a repo you know — its payoff is *freshness you don't
  have to maintain*, not new discovery. If that freshness stops earning its ~18s background cost, drop
  the hook and revert to grep + hand docs; nothing else depends on it.
