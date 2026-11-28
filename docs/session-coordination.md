---
topic: Session coordination (several Claude Code sessions in one repo)
read_when: Starting work while other sessions run; asking "whose worktree is this?"; a "Session guard" warning; the session hooks or the session beads (type session) misbehave
related_issues: [df-0dql, df-2f3j, df-oahj, df-abrb]
---

# Session coordination

Several Claude Code sessions (today ~7, launched by hand; later by an orchestrating session) work
in this repo at once. They learn about each other from facts that already exist (git, bd, a small
registry of live processes), not from messages. Code: `scripts/sessions/` (tests:
`tests/test_sessions_guard.py`, `tests/test_sessions_lifecycle.py`). Status: **built in PR (df-0dql),
active only after the activation steps below.**

## The pieces

| Piece | Where | What it does |
| --- | --- | --- |
| Registry | `<git common dir>/df-sessions/<session_id>.json` | One file per live session: Claude PID + start time, host, actor, session bead, transcript, the worktrees it works in. A cache for the hot path, not history. Dead sessions' files are dropped. |
| Guard | `guard.py`, PreToolUse | Warns (or blocks) when a session writes outside its own worktree(s). Below. |
| Lifecycle | `lifecycle.py`, SessionStart / SessionEnd / SubagentStart / SubagentStop / CwdChanged | Identity, session bead, reaper, short start context. |
| Board | `board.py` | Who works where, derived on demand. |

Unit of coordination: the **worktree** (`.worktrees/<issue>`). An orchestrator's subagents fire no
SessionStart, but each works in a worktree; their tool calls carry `agent_id`, so a subagent's claim
is kept apart from its siblings' while it runs (SubagentStart/SubagentStop mark it running/done).

## Board

```bash
python3 scripts/sessions/board.py                  # text, one block per worktree
python3 scripts/sessions/board.py --json           # same, as JSON
python3 scripts/sessions/board.py --owner PATH     # only: whose worktree holds PATH
#   --no-bd (skip issue + past owners; bd reads take ~6 s on /mnt/c), --no-pr (skip gh), --base REF
```

Per worktree: branch, files changed vs `origin/main` (committed, uncommitted), last commit, the
issue its name carries + that issue's status/assignee, open PR, and **owner**:
`live` (session actor, pid, session bead, messaging socket), else `past` (last session bead that
worked there, when and why it ended), else `unknown`. The main checkout is `shared`.
A worktree whose HEAD has **no common history with origin/main**, or carries `Merge pull request`
commits origin/main lacks, gets `WARNING ... do not push this branch as is` (a branch cut before a
history rewrite).

## Identity

SessionStart appends to `CLAUDE_ENV_FILE` (every later Bash command of the session sources it):
`BEADS_ACTOR=claude-<first 8 of session_id>`, `DF_SESSION_BEAD=<prefix>-sess-<8>`,
`DF_SESSION_ID`, `CLAUDE_SESSION_ID` (bd records it on close). So every `bd` write of the session is
attributed to it, `--claim` sets assignee = that actor, and the claim is the lock between sessions.

## Session bead (index, written only by the hooks)

Type `session`, status `live` (custom, category wip: out of `bd ready`), id `<prefix>-sess-<8>`,
label `session`. Metadata at start: session_id, actor, host, pid, pid_start, cwd, started_at,
source, transcript_path, session_title (only when the session has a custom title),
messaging_socket (`$CLAUDE_CODE_MESSAGING_SOCKET`, the per-process socket cross-session messages use;
the token is never stored), worktrees. At the end: ended_at, end_reason (SessionEnd `reason`, or
`abandoned`), `end` = {worktrees: [{path, branch, commits since start, pr}], beads_created,
beads_closed (closed while assigned to the actor: bd keeps no "closed by"), claims_released}. No prose.
Work knowledge and handoffs go on the **issue** bead.

- Resume (same session_id) reopens the same bead. `/clear` or fork: new session_id, new bead.
- SessionEnd releases the claims still held (back to `open`, unassigned, note in Portuguese), so the
  next session can `--claim` them.
- Reaper (each SessionStart, in the background, under a lock): a `live` session bead of this host
  whose Claude process is gone (PID + start time) is closed as `abandoned`, its claims released.
  Sessions of another host are never presumed dead.
- All bd calls run detached (bd writes cost 1-4 s on /mnt/c; SessionEnd hooks get 1.5 s). The hooks
  leave marker files in `df-sessions/starting/` and `ending/`; `lifecycle.py bg-start|bg-end` takes
  every pending marker (claimed by rename; one held by a dead job goes back to the queue), so a
  crashed job is redone by the next one. Nothing from the hook input reaches a command line.
  Errors land in `<git common dir>/df-sessions/hooks.log` (bd's own `auto-export: git add failed`
  warnings land there too; harmless, `.beads/` is gitignored).

Query the history: `bd list -t session --all`, `bd show <prefix>-sess-<8> --json`.

## Guard (PreToolUse on Edit|Write|MultiEdit|NotebookEdit|Bash)

Reports:
- Edit/Write of a main-checkout file git does not ignore (tracked, or new and unignored);
- Edit/Write in a worktree another live session (or a running sibling subagent) works in;
- Bash `git pull|checkout|switch|reset|merge|rebase|restore|clean|stash|commit|cherry-pick|revert|am`
  or pytest with its directory (hook `cwd`, `cd`, `git -C`) in the main checkout or in another live
  session's worktree; `git worktree remove|move` of such a worktree.

Not reported (exceptions, and why):

| Exception | Why |
| --- | --- |
| Paths outside the repo: scratchpad, `/tmp`, `~/.claude` memory, other repos | not shared through this checkout |
| Gitignored files of the main checkout: the history of `docs/` (`superpowers/`, `phases/`...), `CLAUDE.md`, `.claude/`, `.beads/`, data... | private, live only in the main checkout; this is where they are edited |
| A separate git checkout nested in it (`workspaces/GetFinance`) | its own repo and rules (its CLAUDE.md) |
| The git dir (`.git/...`) | git's own bookkeeping |
| `git fetch`, `git worktree add`, `git push`, reads (`status`, `log`, `diff`) | the documented way to start work; nothing moves |
| The session's own worktrees; a worktree no live session holds (taken on first write, or by `git worktree add`, or `cd` into it) | that is where work belongs |
| A command with `DF_GUARD_ALLOW=1` | the owner asked for it explicitly |

Modes: `DF_GUARD_MODE=warn` (default: the call goes ahead; Claude gets the reason as
`additionalContext`, the user a `systemMessage`), `block` (deny), `off`. Set it in the shell before
`claude`, or under `env` in `.claude/settings.json`. Fails open: any error, timeout or odd input
allows silently (exit 0). Never calls bd; calls git once (`check-ignore`) only for a main-checkout path.

Mid-session worktree changes are recorded by the guard itself (first write, `git worktree add`) and
by CwdChanged (`cd` in the main conversation): both only write the registry file, and only when the
mapping changes. The guard is the reliable trigger (it sees every write, subagents included);
CwdChanged only adds presence before the first write.

Not covered: Bash commands that write files by redirection or `cp`/`sed -i` in the main checkout
(only the git/pytest list above is parsed).

## Cost (measured on /mnt/c, 2026-11-13)

| Hook | Cost |
| --- | --- |
| PreToolUse guard, no-op command | ~70 ms |
| PreToolUse guard, Bash in main checkout / Edit with `check-ignore` | ~100-125 ms |
| SessionStart, synchronous | ~0.3 s alone; plus ~0.75 s `git status` per other live worktree, in parallel; first run ever +1-6 s (bd prefix lookup, then cached) |
| SessionStart background (bead create + reap) | ~6 s, detached |
| SessionEnd, synchronous | ~0.1 s |
| SessionEnd background (record + close) | ~5 s, detached |

## Activation (one time, after the PR merges)

1. Update the main checkout to include the merge (the hooks run `$CLAUDE_PROJECT_DIR/scripts/sessions/hook.sh`).
2. On the real bd database (main checkout):
   ```bash
   bd config set types.custom session      # warns "not a recognized config key" but works (bd 1.0.3)
   bd config set status.custom "live:wip"
   ```
   If `types.custom` already holds other types, keep them (comma-separated). Until this runs, the
   hooks do no harm: `bd create -t session` fails and nothing is written.
3. Add to `.claude/settings.json` (merge with the existing `hooks`):
   ```json
   "SessionStart": [{"hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/sessions/hook.sh start", "timeout": 30}]}],
   "SessionEnd": [{"hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/sessions/hook.sh end", "timeout": 5}]}],
   "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/sessions/hook.sh guard", "timeout": 10}]}],
   "SubagentStart": [{"hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/sessions/hook.sh agent-start", "timeout": 5}]}],
   "SubagentStop": [{"hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/sessions/hook.sh agent-stop", "timeout": 5}]}],
   "CwdChanged": [{"hooks": [{"type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/scripts/sessions/hook.sh cwd", "timeout": 5}]}]
   ```
   Sessions started before this keep running without SessionStart; the guard registers them on
   their first guarded call (no bead until their next start).
4. Add to `CLAUDE.md` (section "Git", after the worktree rule):
   ```markdown
   - **Other sessions.** Hooks track who works where (`docs/session-coordination.md`). Before touching a
     worktree you did not create, ask `python3 scripts/sessions/board.py --owner <path>`; the full
     board is `board.py` (`--json`). A "Session guard" warning means the call wrote outside your own
     worktree: stop and move the work, unless the owner asked (`DF_GUARD_ALLOW=1`). Your bd writes are
     attributed to `$BEADS_ACTOR`; handoffs go on the issue bead, never on the session bead.
   ```

Sessions must start in the main checkout: `.claude/` is gitignored, so a session started inside a
worktree loads no project hooks.

## Not verified (needs a live session after activation)

See the follow-up beads `discovered-from:df-0dql`: whether the hook process sees
`CLAUDE_CODE_MESSAGING_SOCKET` (the PID comes from `$CLAUDE_PID`, documented for hooks); whether
subagents' Bash sources the parent's `CLAUDE_ENV_FILE` (then they write as the parent's actor);
that the detached closer survives the terminal closing; whether child sessions
(`CLAUDE_CODE_CHILD_SESSION`) fire SessionStart.
