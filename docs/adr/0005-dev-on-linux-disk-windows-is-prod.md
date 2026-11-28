# Develop the core on the Linux disk; the Windows folder is prod only

**Status:** accepted (2026-11-13). Decision: bead df-7aut. Setup and migration: df-b86q. Prod install: df-qryy.

The core used to be developed in the same checkout that prod ran from, on the Windows drive that
WSL mounts over 9p (`/mnt/c/...`). Two problems: 9p is slow, and dev and prod shared one working tree
(a `git pull` there was a deploy, and test leftovers landed in prod).

Measured on the same commit and machine (df-7aut notes; df-b86q):

| | Windows drive (`/mnt/c`) | Linux disk (ext4, `~`) |
| --- | --- | --- |
| engine tests (non-GUI, ~1050) | 66-73 s | 35-42 s |
| new venv with all deps | 438 s (pip) | ~1 s (uv, warm cache) |
| `git worktree add` | 3.0 s | 0.03 s |
| quality gate, full | ~7 s | ~2.5 s |
| `bd ready` / `bd note`, by where the DB lives | 4.3 s / 3.7 s | 1.1-1.3 s / 0.95 s |

`bd` gets faster only when the database itself moves (the flip below); through the redirect it costs
what it costs on C: today. The Show pipeline (19 tasks) runs in about 8 s from the clone.

## Decision

- **Dev home:** `~/src/DataForge`, a `git clone` of origin. Its venv is built with `uv`. Every
  worktree lives under `~/src/DataForge/.worktrees/<issue>` and has its **own** `.venv`
  (`scripts/dev/new-worktree.sh`), so `dataforge` and `import dataforge` run the worktree's code.
- **Prod:** the Windows folder (`C:\...\DataForge`, `/mnt/c/.../DataForge` from WSL) keeps only what
  prod needs: `workspaces/` (the real `GetFinance` workspace, its own private git repo), `dataforge.db`
  and `.venv-prod`, a non-editable install that the Task Scheduler runs. It is updated only by
  `scripts/prod/update-prod.ps1`. **Nobody runs `git pull`, tests or edits of core code there.**
- **The real GetFinance workspace stays on Windows** (its data, Excel COM and the Task Scheduler are
  there; df-sgyf). Work on it keeps happening on the Windows path, under that workspace's own
  `CLAUDE.md`. Its repo has pre-commit and commit-msg hooks (gitleaks + `tools/leakguard.py`) for all
  its worktrees; a branch made before they landed needs `git fetch origin && git rebase origin/main`
  before its first commit.
- **`DATAFORGE_HOME` for dev is the clone.** The clone carries the synthetic `workspaces/GetFinance-Show`
  (zero credentials), which is what dev runs. Never point a dev session's `DATAFORGE_HOME` at the
  Windows folder: that is prod's home.
- **One beads database.** It moves to ext4 in a single flip at the end of the migration (below), with
  its backup on C:. Until then it stays on Windows and the clone reaches it through
  `.beads/redirect`.

## How each piece is shared

| Piece | Now (migration) | After the flip |
| --- | --- | --- |
| beads DB | `<win>/.beads`; the clone has `.beads/redirect` -> it | `~/src/DataForge/.beads`; `<win>/.beads` becomes a redirect -> it; `bd backup sync` to C: |
| git history | origin (GitHub) | same |
| private, unversioned files (`CLAUDE.md`, `docs/`) | copied into the clone on 2026-11-13; step 0 below makes the clone the only copy and leaves symlinks on Windows. Until then the two copies **diverge** if both are edited | only in the clone |
| per-checkout state (`.claude/`, `pytest.ini`, `.mcp.json`, `.leakguard-local`, `scripts/quality/baseline.local.json`, `.testloop/`) | copied once; each checkout keeps its own | only in the clone |
| `.leakguard-workspaces` | in the clone: lists the real workspace on C: so commits from ext4 are checked against its real values | same |
| Claude Code memory dir (`~/.claude/projects/<path-key>/memory`) | keyed by the start directory: a session in `~/src/DataForge` gets a new, empty one | link it to the old one, or let it start empty (`bd memories` is the source of truth) |

**Why `.beads/redirect` and not `BEADS_DIR` or a symlink:** all three were tested on a copy of the
database (on C:, reached from ext4). The redirect is a file in the clone's own gitignored `.beads/`,
needs no environment in every shell or hook, is followed by `bd` from every worktree of the clone
(git common dir), and changes nothing inside the real `.beads`. Removing it undoes it.
`BEADS_DIR=<path>` works too and is the fallback.

**Evidence that sharing is safe:** a note written from ext4 and one from `/mnt/c` both showed on both
sides; 8 concurrent `bd note` processes (4 from each side) all exited 0 and all 8 notes were there.
Every `bd` process, on either side, is a WSL process that opens the same files through the same
kernel, so the embedded Dolt lock serializes them exactly as it does today between worktrees.

## Migration, one session at a time

Old and new locations work side by side: same origin, same tracker. Nothing forces a session to
move before it is ready.

**Step 0, once, before the first session moves (needs the owner or the coordinator, because it
touches the Windows folder):**
1. Merge the df-b86q PR (`.leakguard-workspaces`): without it, a commit made from the clone is not
   checked against the real workspace's values.
2. Make the clone the only home of the private docs: bring over any edit made on Windows since
   2026-11-13, then replace Windows' `CLAUDE.md` and `docs/` with symlinks to the clone's, and add
   `docs` to that checkout's `.git/info/exclude` (a symlink does not match the `docs/` ignore rule).
3. Optionally link the Claude Code memory dir of `~/src/DataForge` to the old one (below).

**Per session:**
1. In the Windows worktree: commit and push the branch. Nothing may exist only there.
2. Remove that worktree (`git worktree remove .worktrees/<issue>` from the Windows checkout).
3. Start the new session in `~/src/DataForge`.
4. Recreate the worktree from the pushed branch:
   `scripts/dev/new-worktree.sh <issue> <branch> origin/<branch>`, then `git push -u origin <branch>`
   on the next push.
5. `bd` needs nothing: the clone's `.beads/redirect` points at the shared database.

A session doing real-workspace work (GetFinance data, Excel, the workbook) stays on the Windows path.

## The database flip (last, when no session uses the Windows checkout for core work)

Precondition: df-hsx8 done (a periodic `bd backup sync` to C:). Then, with no `bd` process running:

1. `bd backup sync` and `bd count` (note the number).
2. `mv ~/src/DataForge/.beads ~/src/DataForge/.beads.redirect-old`
3. `cp -a <win>/.beads ~/src/DataForge/.beads`
4. From `~/src/DataForge`: `bd where` (no "via redirect"), `bd count` equal to step 1, `bd doctor`,
   `bd backup status` (the destination on C: is in the copied `dolt-backup.json`).
5. `mv <win>/.beads <win>/.beads.moved-<date>`, then `mkdir <win>/.beads` and write the clone's
   absolute `.beads` path into `<win>/.beads/redirect`, so anything still started on the Windows path
   reaches the moved database.
6. `bd note` from both sides, then `bd backup sync`.

Undo: remove `<win>/.beads`, rename `.beads.moved-<date>` back, rename `.beads.redirect-old` back.
After the flip, retire `.venv-win` and remove the core source from the Windows folder (df-qryy),
which leaves it with `workspaces/`, `dataforge.db` and `.venv-prod`.

## Consequences

- A session decides where it works by where it starts: core work in `~/src/DataForge`, real-workspace
  work on the Windows path. Two homes, one tracker.
- The WSL disk (VHDX) is outside the Windows backup and `.beads` has no remote. The DB flip waits for
  a periodic `bd backup sync` to C: (df-hsx8).
- **Tests split by repo.** Core (engine) tests live in DataForge and run in the clone on ext4. The
  GetFinance pipeline tests belong to the private GetFinance-Workspace repo (`tests/` there, fake
  values, its own CI) and run from the Windows path. df-rt6w moved them (merged 2026-11-13); the old
  copies under DataForge's gitignored `tests/workspaces/` and `tests/test_finance_daily_dag.py` were
  archived to `projects/_archive/`.
- `update-prod.ps1 -Ref` needs a checkout that Windows git can read. Once core code leaves the Windows
  folder, update prod from a wheel built in the clone (`docs/commands.md`).

## Revision 2026-11-17 (df-aef7): GetFinance is developed on the Linux disk too

Decided with the owner. It replaces, above, "the real GetFinance workspace stays on Windows ... under
that workspace's own `CLAUDE.md`", "two homes, one tracker" and "run from the Windows path".

- **One home for sessions.** Every Claude session starts in `~/src/DataForge` and works on both
  projects. The Windows folder holds no `CLAUDE.md`, `.claude/`, `.mcp.json` or `.beads/redirect`
  (archived to `projects/_archive/DataForge-win-claude-2026-11-17`); the GetFinance repo has a
  `README.md`, not a `CLAUDE.md`. Why: a session started on the Windows path got DataForge's rules
  without its hooks (df-nxlo) and wrote to the same bd database.
- **GetFinance dev is a clone at `~/src/DataForge/workspaces/GetFinance`** (gitignored here), with
  worktrees under `.worktrees/getfinance/<issue>`. The Windows checkout is prod: the daily job, the
  real data, and code only by `git pull --ff-only`.
- **The real data is read from the clone, never written:** `GETFINANCE_DATA` names the Windows
  workspace's `data/` for the `integration` tests and the leak guard, which fails closed without it.
- Rejected: a `CLAUDE.md` one directory above both projects. It moves `CLAUDE_PROJECT_DIR`, which
  every hook path, the `.beads` lookup and claudemain's mirror depend on.
- Direction, not yet done (df-bsb2): the GetFinance code becomes public inside this repo, in place of
  `workspaces/GetFinance-Show`.
