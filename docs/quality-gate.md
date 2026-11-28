---
topic: Quality gate (lint, dead code, import contracts, dependency drift against a baseline)
read_when: A commit, CI run or Claude Code hook fails with "quality gate"; adding or tuning a lint rule; suppressing a finding; changing scripts/quality/
related_issues: [df-q6oj]
transversal: true
---

# Quality gate

`scripts/quality/gate.py` keeps code quality from getting worse without first fixing every old
finding. It runs five checkers, counts what they find per file and rule, and compares the counts
with a committed baseline. **A count above the baseline fails. The baseline only goes down, and
lowers itself:** when a count falls, `check` writes the lower number (pre-commit stages it into the
same commit). Nobody maintains it by hand; a new file starts at zero, so anything in it is new.

| Checker | What it catches | Config |
| --- | --- | --- |
| ruff | bugs (including a function that returns a value on some paths and falls off the end on others, RET503), swallowed errors, complexity, magic values, inline imports, `os.environ` in the package, requests without timeout, unsafe YAML/SQL/shell | `[tool.ruff]` in `pyproject.toml` |
| vulture | dead code: unused functions, methods, attributes, variables | `[tool.vulture]` |
| import-linter | layer contracts: core does not import CLI/API/GUI; the API does not import the CLI; the GUI talks HTTP, not core; only the GUI imports Qt | `[tool.importlinter]` |
| deptry | dependency drift in published code outside `tests/`: an import nothing declares (DEP001), a declared dependency nothing imports (DEP002, reported on `pyproject.toml`), a transitive dependency imported directly (DEP003). Full gate only. Script modules that import each other as siblings (`scripts/testloop/common`, a workspace's `report_lib`...) are passed as first-party by the gate | `[tool.deptry]`; each exception has a comment saying why |
| project rules (in `gate.py`) | **DF001** a suppression comment with no reason; **DF002** a `sqlite:///` URL spelled outside `dataforge/compat.py`; **DF003** `read_delta`/`scan_delta` named in a workspace app's backend (`workspaces/<ws>/apps/<app>/backend/`): it reads Delta through `dataforge.delta_pool` (df-5lk8, df-vpo3; the package itself is covered by ruff TID251); **DF004** (full gate only) a file nothing calls, which vulture cannot see because a whole file has no unused name: a workspace script (`workspaces/<ws>/scripts/**`) that no `job.yaml` runs and no other file of the workspace outside `tests/` imports or names as `"<name>.py"` in a string, and a module of `dataforge/` that no other module imports or spells as `dataforge.x.y` and that `pyproject.toml`, `.github/workflows/` and `scripts/` do not name; tests never keep a file alive. A script run by hand on purpose says so on a line of its own: `# gate: manual-script -- why` (df-aq85) | `gate.py` |

## Commands

```bash
python scripts/quality/gate.py check                 # full gate (about 2.5 s on ext4, 7 s on /mnt/c)
python scripts/quality/gate.py check FILE...         # fast: ruff + project rules on these files
python scripts/quality/gate.py check --full FILE...  # full gate, judging only these files
python scripts/quality/gate.py update --allow-increase  # accept new findings, on purpose only
uv sync                                              # the pinned tools (`quality` group); the gate refuses other versions
```

`ruff check` alone shows every finding, old ones included. The gate shows only what is new.

## Where it runs

| When | What | Notes |
| --- | --- | --- |
| Claude Code, after each Edit/Write of a `.py` | fast gate on that file | exit 2 feeds findings back to Claude; `scripts/quality/hook.sh` (see "The Claude Code hook" below) |
| Claude Code, end of each turn | full gate on the files this session edited, once per checkout | another session's work in progress never blocks it; one retry, then the turn ends |
| `pre-commit` | full gate | isolated env with the same pins; runs when a `.py` file or `pyproject.toml` is staged |
| CI job `quality` | full gate, plus `--base-ref origin/<base>` on PRs | fails if the PR raised the committed baseline; the `report` job comments every job's result on the PR (`scripts/ci_report.py`) |
| CI job `sonar` | SonarQube Cloud (`sonar-project.properties`), with the `coverage.xml` from the `tests` job | a complement, not a replacement: coverage of new code, cognitive complexity, duplication, history. Not required yet; skipped when `SONAR_TOKEN` is absent (df-ahua.1). The workflow runs one at a time per ref (`concurrency`): main runs queue so Sonar gets their analyses in commit order, since it refuses an older commit's analysis after a newer one (df-huj7) |

## Stale docs (a warning, never a failure)

Every `check` also prints, from `scripts/quality/docs_parados.py` (df-8x0m.23.39.3):

- `docs: N of M in sketch (status esboço)`: the docs under `docs/` with a front matter, and how
  many are still sketches.
- `warning: <doc> was not changed, but the code it covers was: <files>` when the branch changed a
  file that the doc's `code:` covers and did not change the doc. Changed = the diff against the
  merge-base with `origin/main`, committed or not, plus untracked files. A file covered by several
  docs warns only the most specific one, the doc whose matching `code:` path is the longest
  (df-ev4u): a container doc with `code: [dataforge]` stays quiet when a component doc covers the
  file. Without a merge-base the check says it was skipped.

The final gate line carries the count: `quality gate: ok (N findings, none new; K stale docs)`
(`1 stale doc` when there is one), so the CI report and the PR comment show it. If the check itself
errors, the gate prints `docs: stale-doc check failed: <error>`, drops the suffix and goes on: it
does not fail the gate.

The warning never changes the exit code nor the baseline: read it, and update the doc when the
change made it wrong. Tests: `tests/test_docs_parados.py`.

## The Claude Code hook

`scripts/quality/hook.sh` hands each edited file to the gate **of the checkout that holds it**: a
file under `.worktrees/<issue>/` is judged by that worktree's `gate.py`, rules and baseline, with
that worktree's venv (the clone's venv when the worktree has none). A gate only sees the files of
its own checkout, so before df-5wsy the clone's gate answered `ok` for every worktree file without
looking at it. A file outside git, or inside a private workspace that is its own repository, goes
to the clone's gate.

- The hooks are wired in the clone's `.claude/settings.json`, so they fire in sessions **started in
  the clone** (`~/src/DataForge`), wherever the edited file is. A session started inside a worktree
  loads no project hooks (a worktree has no `.claude/`): there, `pre-commit` and CI are the gate.
- Tests: `tests/test_quality_gate_hook.py`.

## When it fails

1. **Fix the finding.** That is the default.
2. **A deliberate exception:** suppress it on its line, with a reason after ` -- `:
   `from dataforge.gui import run_app  # noqa: PLC0415 -- lazy: keeps CLI startup fast`.
   A suppression without a reason is itself a finding (DF001).
3. **Dead code a framework calls** (a decorator, a Qt override, an attribute a library reads): add
   the decorator or name to `[tool.vulture]`.
4. **An import a contract forbids:** fix the import, or add an `ignore_imports` entry with a
   comment saying why. Contracts are never baselined.
5. **A dependency finding (`deptry:DEP...`):** declare what the code imports, drop what nothing
   imports, or import from the package you declare (e.g. `fastapi.datastructures`, not `starlette`).
   A real exception (a package loaded at run time by name, like `deltalake` through polars) goes in
   `[tool.deptry] per_rule_ignores` with a comment. deptry is at zero findings; keep it there.

`update --allow-increase` accepts new findings. Use it only on purpose (for example after adding a
rule): CI flags the raise against the base branch.

## Baselines

- `scripts/quality/baseline.json` — published files. Committed.
- `scripts/quality/baseline.local.json` — gitignored files (private workspaces, private tests).
  Gitignored, so their paths never leave the machine. CI never sees them.
- A private workspace that is its own git checkout (until df-bsb2.2.7, the main clone's
  `workspaces/GetFinance`, the dev clone of the private `GetFinance-Workspace` repo; the published
  workspace of the same name is tracked by this repo and read like any other file, its `tests/` as
  tests) is read through that repository's own `git ls-files`: its tracked and
  untracked files, minus what its own `.gitignore` excludes (`data/`, `.venv/`...). Before df-grvi
  such a workspace was invisible to the gate, which reported `ok (0 findings)` without checking it.

Lowering is automatic and never raises a count: a file with new findings keeps its old count, so
`check` still reports it, and someone else's work in progress is never blessed. A commit made
without the hook can carry a baseline that is higher than needed; the next `check` lowers it.

## Adding a rule

1. Add the code to `select` in `[tool.ruff.lint]` (preview-only rules need the exact code).
2. Run `ruff check --statistics` to see the count, and sample a few to judge the signal.
3. Mandated patterns go in `per-file-ignores` with a comment (e.g. `F821` under `workspaces/**`, for
   the names the `exec(...)` loader of `functions.py` brings in).
4. `python scripts/quality/gate.py update --allow-increase`, then commit the baseline with the rule.

## Gotchas

- vulture reads a comment starting with `# type:` as a type comment and, if it is not valid
  Python, skips the **whole file** (reported as `vulture:parse-error`), which hides its usages and
  creates false positives elsewhere. Reword such comments.
- Workspace scripts get names from `exec((... / "functions.py").read_text(...))`, invisible to pyflakes,
  so `F821` is off under `workspaces/**`; an undefined name there shows up when the pipeline runs.
- deptry decides by what is installed: where the runtime packages are not (CI's `quality` job, the
  pre-commit env), a transitive import reads as DEP001 instead of DEP003, and a distribution's import
  name falls back to its own name. That is why `[tool.deptry.package_module_name_map]` spells out
  `pyyaml`→`yaml` and the like. Check a deptry change in both kinds of env. After this landed, a
  local gate needs the `quality` group installed (`uv sync` does it; the gate refuses to run without deptry).
- Tool versions are pinned in the `quality` dependency group of `pyproject.toml` and in `.pre-commit-config.yaml`;
  a different ruff version finds different things, so the gate checks the versions first.
