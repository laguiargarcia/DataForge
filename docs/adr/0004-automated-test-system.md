# Automated test system: tools generate and judge, the LLM thinks

**Status:** direction approved by the owner (2026-11-04); reorganized afterwards to put the general
flow first. Tracked in beads (df-886).

Decisions marked **[owner]** came from the owner. Those marked **[call]** are the author's
proposals, open to correction. Tool names are candidates, to be confirmed in a spike, unless a
section says otherwise.

Part 1 is the general system and could apply to any Python repo. Part 2 is how it applies here.

# Part 1: the general system

## Why

Having an LLM write every test by hand is a poor use of it: the time goes on typing cases rather
than on ideas and review. Tests exist to catch bugs. **[owner]** We want non-AI tools that
generate and evaluate tests automatically, combined with an LLM guided by a skill, so that we
catch more bugs, write more tests and review more in less time.

## Goals

1. See every branch: which `if/else` paths ran, and which test reached them. **[owner]**
2. Tell "missing test" apart from "code nothing reaches". **[owner]**
3. Generate cases nobody thought of, without writing them one by one. **[owner]**
4. Measure whether the tests would notice a bug, not only whether they ran the code. **[owner]**
5. Fast feedback from running only what a change affects, designed in from day one. **[owner]**
6. Pass/fail in the deterministic tier gives the same result every time, with no per-run token cost.
   Real-data tests and live API checks report separately and do not meet this bar. **[call]**
7. Measure whether the LLM with the skill beats the LLM alone. **[call]**

## Non-goals

- Replacing human review of the rules that matter.
- Load or performance testing.
- Covering every tool in the brainstorm (see "Not now").

## Anti-goals (failure even if everything is green)

- Chasing a global coverage percentage, or tests that execute code without checking anything.
- The LLM deciding whether something passed.
- A flaky suite that people learn to ignore.
- Deterministic tests that use the network, real credentials or real financial data.
- Recorded API responses containing personal data or secrets.
- Many near-identical helpers or tests, or one generic function full of per-caller flags.

## The flow

```
target: module, change or bug
  1. MAP        which branches run, and through which test
  2. LLM        reads the gaps and proposes rules
  3. GENERATOR  creates cases; a failure is a real bug and a fixed case
  4. MUTATION   survivors go back to step 2
  5. JUDGE      deterministic command: passed or not
  6. BEADS      only triaged suspicions become issues
```

| Part | Question | Who | Candidate |
|---|---|---|---|
| Map | Where did execution go, and which test went there? | Branch coverage with per-test contexts | coverage.py |
| Generator | Which cases did nobody think of? | Properties, action sequences, API schema | Hypothesis, `polars.testing.parametric`, Schemathesis |
| Evaluator | If the code breaks, does anyone notice? | Mutation testing | mutmut, Cosmic Ray, pytest-gremlins (pick by spike) |
| Brain | What does this gap mean, which rule is missing? | LLM guided by a skill | Claude Code skill |
| Judge | Did it pass? | The tools only, run automatically | One local command, CI, SonarQube gate, scheduled runs |

**Reading the map.** A branch tests did not reach means little alone, so test coverage is crossed
with coverage of real runs. Both must measure the same code. **[call]**

| | Real run reaches it | Real run does not |
|---|---|---|
| **Test reaches it** | Healthy | Possibly unused: candidate for removal |
| **Test does not** | **Highest risk:** used and unprotected | Dead code or rare failure |

The LLM triages the last cell into: unreachable (remove), rare failure (fault injection), or
another OS (measure there and merge).

## The skill

**[owner]** The skill and the flow are the focus. **[call]** Scripts measure, the LLM interprets, tests
decide.

| Piece | Who | Result |
|---|---|---|
| `map` | Script | JSON with uncovered branches and the tests reaching each line |
| `mutate` | Script | JSON with surviving mutants |
| `check` | Script | Passed or not, always the same |
| Interpret and propose rules | LLM, using the property-testing skill | New tests, or "this code can go" |
| Stop | Skill | No relevant gap left, or round limit reached |

The skill accepts a module, a change or a bug. It may write tests and rules, but it never marks a
result as passed. Each bug found is generalized (see below). Anything specific to a repo goes in one
per-repo config file: paths, test command and declared dependencies.

## Existing plugins

No plugin found covers the whole loop, so ours stays thin and reuses what exists. **[call]**

| Plugin | Use |
|---|---|
| Trail of Bits `property-based-testing` | Install, after reading it in full. Writes and reviews properties, with Hypothesis support |
| Trail of Bits `mutation-testing` | Reference only: built for mewt and muton, Python support not confirmed |
| `skill-creator` (official) | Build the skill and measure it with evals |
| `sonarqube` (official) | Evaluate: runs analysis after edits through a hook |
| `pr-review-toolkit` (official) | PR review, separate from deterministic tests |
| `playwright` (official) | When web screens exist |
| Community mutmut skills | Reference only, not verified |

The Trail of Bits repo is CC-BY-SA-4.0, which matters if we adapt its text instead of installing it.

## Other parts of the system

**Chosen by the owner:**

- **Run only what matters.** See "Test selection".
- **Generalize every bug.** A bug becomes the most general protection that fits: a code pattern
  becomes a repo-wide rule, a business rule becomes a property, and only a truly specific case
  stays a single fixed test.
- **Generated data.** Single-table transformations use generated frames. Multi-table and end-to-end
  flows use a synthetic dataset owned by the tests, because generated frames do not guarantee
  relations between tables. **[call]**
- **SonarQube Cloud on pull requests.** Ruff and Pylint stay the main checkers.

**Proposed by the author, accepted by the owner:**

- **Data validation in the pipeline.** Rules checked on every run, before the write they protect, in one
  shared function. Impossible data stops the job and alerts. Suspicious data alerts and continues.
  Candidate: Pandera.
- **Data mutation.** One generic mutator corrupts a dataset (nulls, duplicate keys, flipped signs,
  broken encoding, shifted dates, empty input, missing column) and asserts that the checks notice.
- **Crash at every point.** A sweep kills the work at the 1st, 2nd, Nth write and checks recovery.
  Use a real process exit (`os._exit` in a subprocess) rather than only a raised exception. No
  Python library was found, so expect a small in-house helper.
- **Conventions as rules.** Written rules become automatic checks. Candidates: Semgrep, import-linter.
- **Coverage of the change only.** New or changed code cannot land with an uncovered branch unless
  an exception states its reason. There is no global target. Candidate: diff-cover.
- **Flaky-test hunting.** Random order and repeats. A prerequisite for trusting mutation results across
  a whole suite. Candidate: pytest-randomly.
- **Types and lint**, blocking on new or changed code only. Candidates: Ruff, Pyright.
  - *Measured 2026-11-13 (df-ahua.9): Pyright stays out for now.* Pyright 1.1.414, standard mode, on
    `dataforge/` and `scripts/` with the project installed as CI installs it: 121 findings in 6 s.
    By rule: attribute access 33, argument type 30, optional member access 26, possibly unbound 18,
    general type 7, return type 4, operator 2, call 1. Triage: **2 real** (one defect, df-ahua.9.1:
    `deploy rollback` crashes if another actor empties the ledger while it waits at the prompt),
    **58 true but harmless** (18 of them from one `-> None` that should be `NoReturn`), **61 library
    noise**: 38 from the `Column()`-style SQLAlchemy models in `db.py` (every `row.status = ...`
    reads as assigning a `str` to a `Column[str]`) and 23 from PySide6 stubs (`bytes(QByteArray)`,
    Qt getters typed `| None`). The noise sits in the core error rules, so configuration cannot
    remove it without turning those off, and it recurs on every new ORM write or Qt call: a gate
    would fail on normal new code. The findings also depend on the installed packages (without them,
    86 of 127 are unresolved imports), so the pre-commit env and CI's `quality` job would need the
    whole runtime, PySide6 included. mypy 2.4.1 showed the same noise (88 lines) and missed the
    rollback defect. **Would change the decision:** the models moving to `Mapped[]` columns
    (df-ahua.9.3), which removes the largest noise class; then measure again.
- **Record and replay external APIs**, with scrubbed responses, plus a scheduled check against the
  real API so recordings do not age silently. Candidates: VCR.py, pytest-recording.
- **Unused-code report**, never blocking, because routes and YAML-called scripts cause false
  alarms. Candidates: Vulture, deptry.

## Suspicions live in beads

**[owner]** Suspicions and improvement ideas live in beads, so everything is in one place. They never
block a change. Signals are crossed to rank them: complexity, coverage, surviving mutants, duplication.

To keep the tracker usable: **[call]**

- **Raw tool output stays in files.** The tools report thousands of findings, so only ranked and
  triaged items become issues, with a limit per round.
- **The LLM reads the code first** and writes why the item is suspicious and what could improve.
- **`bd ready` stays clean.** Issues carry the label `suspeita` and are deferred, or filtered with
  `--exclude-label suspeita`, until someone accepts them.
- **No duplicates between rounds.** `--external-ref` holds a stable fingerprint of tool, rule, file
  and function, not the line number. `bd list` has no filter for it, so de-duplication parses its output.
- **Issues follow the code.** A finding that disappears closes its issue. A rejected finding is
  remembered and does not come back.
- **An accepted suspicion becomes a normal bug or task** and loses the label.

## Testable code without repetition

**[owner]** Only high-risk rules move out of flat scripts into functions; everything else stays as it
is. Scripts still read top to bottom and call the function.

**[owner]** Do not repeat code. Prefer one somewhat generic function over several specific ones.

Working rules: **[call]**

1. Before extracting, look for an existing function with the same shape and reuse or extend it.
2. Differences between callers become data parameters (columns, keys), not behaviour flags.
3. If a function starts needing per-caller branches, those are different rules: split them. This
   is the only reason to have more than one.
4. Tests follow the same rule: one parametrized property suite, not a copy per caller.
5. A duplicate-code report runs in the scheduled round, without blocking. Candidate: pylint's
   duplicate-code check.

## Test selection

Built from the start, because reorganizing tests later is the hard part: **[owner]**

- **Tests separated by kind:** fast, pipeline, GUI and long rounds, by folder or marker.
- **Python selected from the map**, which records which test reaches each line. A tool such as
  testmon may replace this if a spike shows it sees enough.
- **Invisible dependencies are declared** in one file per repo, such as YAML or scripts run in a
  subprocess.
- **When in doubt, run more.** A shared config change or a file with no known owner runs everything.
- **Selection is audited.** The full suite still runs on schedule. A failure the selection skipped
  counts as a selection bug and its rule gets fixed.

## Rounds

**[owner, 2026-11-12, df-ahua.14]** There is no "run everything at night" round. Code does not change
overnight, so repeating deterministic checks on the same commit finds nothing. Each check runs when
its input changes, and no check produces a report nobody reads: a finding fails a job or becomes a bead.

| What changes | When it runs | Contents |
|---|---|---|
| The working tree | Every change, through one command (`scripts/quality/gate.py check`, also the pre-commit hook) | Ruff, Vulture, import contracts, deptry and the project rules, against the baseline. `scripts/testloop/cli.py diff-cover` shows uncovered changed lines before a PR |
| The code, in a PR | Each pull request, in CI | Full engine suite in random order on Ubuntu and Windows, GUI tests, crash sweep and data mutation (they are ordinary tests), mutation on the targets the change affects, leak scan, quality gate, SonarQube gate |
| The code, on main | Each push to main | The same, with mutation on every target, which audits what PRs skipped |
| The world, without a commit | Weekly cron in `ci.yml` | New dependency releases (the whole CI), and `flaky-hunt`: the engine suite five times with fresh random seeds |
| Real data and the real API | Every run of the pipeline, on the owner's machine | `validate()` before each write: the raw-input rules are the live check of Pluggy's payload; reconciliation between layers. Impossible data fails the job, suspicious data is listed by `data_warnings` |

Test selection for the pytest suite was dropped (df-ahua.13): the suite takes under two minutes, and
selection is the one piece that could skip a test that would have caught something. Selection stays
where it pays, in mutation (`scripts/testloop/affected.py`).

The fast round should stay short enough that nobody skips it. **[call]** One command runs it, called by
the skill or agent before finishing a task and by a git pre-commit hook. A git hook alone is not enough,
because agent work avoids git during a task.

## First slice

**[owner]** Start small: map, properties, mutation and the skill, on `dataforge/deploy/plan.py`. It is
public code and runs `tests/test_deploy_plan.py` and the properties file, so it should not hit the full-suite segfault.

The slice must show three things: **[call]**

1. The loop runs end to end on one module.
2. Mutation works with the chosen tool on this code.
3. Evals compare the LLM alone with the LLM using the skill, by mutants killed and time spent.

Everything else in this document waits for what the slice teaches.

**Mutation tool:** mutmut 3.8.1 works on `plan.py`. 223 mutants, 156 killed, 67 survived, in about 25 s for
the mutmut run itself. It mutates a copy (`mutants/`), the tests import that copy rather than the editable
install, and a mutant applied by hand fails 4 of 15 tests. Cosmic Ray was not needed. Only
`scripts/testloop/mutate_cmd.py` knows mutmut. Real time of `mutate` on `plan.py` is about 2 minutes
(baseline 112 s), most of it one `mutmut show` per survivor to get the diff.

**First cycle on `plan.py`:** mutation score went from 0.6996 to 0.8834 and the cycle found 4 bugs
(`df-886.8`, `.9`, `.10`, `.11`). All 4 came from an LLM reading the code while classifying surviving
mutants, not from a generator failing by itself. This contradicts part of the design, which expected
generators to find the cases nobody thought of: here the reading found them, and the generators only
protected them afterwards.

**Comparison, LLM alone vs LLM with the skill** (`scripts/testloop/evals/RESULTS.md`; 3 runs per arm on
`plan.py`, same model, 8 seeded bugs, rules fixed in `PREDECLARED.md` before any run):

| Measure (median) | alone | skill |
|---|---|---|
| Seeded bugs caught directly | 3 | 4 |
| Mutation score | 0.7892 | 0.9013 |
| Seconds | 59 | 424 |
| Cost USD | 0.14 | 0.27 |

Verdict by the predeclared rule: **not worthwhile**, because the time cap failed (7.19 times the alone
median, cap 2 times). Both quality clauses were met, but by thin margins: seeds by exactly 1 (driven by
seeds 04 and 08) and mutation score by 11.2 points against a 10-point threshold. The 2x cap was the
author's choice. Whether a 7-minute run is acceptable is for the owner to decide; the verdict above is
what the rule gives and was not changed.

**What the data contradicts or leaves open:**

- Only 3 repetitions per arm, on the module where the skill was developed. It says nothing about other
  modules.
- Seeds 05, 07 and 09 were caught by no run in either arm.
- Generated rules only mapped a folder to itself; renamed bases and exact-file rules went unexplored.
- No run, in either arm, found `df-886.8` to `.11`, the bugs the first cycle found.
- The alone runs did not use mutmut or hypothesis although available, so the arms differ in method and
  in tooling at once.
- All three skill runs made no `trailofbits` tool call and no `Skill` call, so `test-loop` was measured
  without its named property skill. Whether it triggers by itself was never tested (the prompt gave the
  path), the `bd` steps were not exercised (the copies have no database), and the skill was edited
  slightly after the runs, which did not use those edits.

## Not now

Each comes back only when a concrete problem justifies it:

HypoFuzz, Playwright (with df-dgq), pytest-qt (PySide stays as admin for now, but df-dgq may replace
it), time-machine, LLM-suggested mutants, CrossHair, SlipCover. Rejected: Atheris
(HypoFuzz fits pure Python better) and Pynguin (it freezes current behaviour, bugs included).

## Left to the builder

Tool choice among candidates (by spike), folder layout, file formats, scheduling mechanism, order
of work after the first slice, the mutation scope per round, and the fast round's time limit.

# Part 2: applying it to this project

The main risk here is wrong data that raises no error (finance pipeline) and lost state after a
failure (deploy, job runs), more than code that crashes. **[call]**

## Repositories

**[owner]** A test lives with the code it tests.

| Where | Visibility | Contents |
|---|---|---|
| `DataForge` | Public | Engine, Show, engine tests with fake data only, CI that runs them |
| `GetFinance-Workspace` | Private | Workspace scripts and configs (single branch `main` since 2026-11-12), pipeline tests |
| Owner's machine | No repo | Real data, `.env`, `DataForge-QA`, beads, repo-root `CLAUDE.md` and `docs/` |

D9 (`2026-06-21-clean-show-repo-migration-design.md:38`, cited by ADR 0001) kept tests private to avoid
sanitizing them during the migration, not because tests must be secret. Fake data removes that reason,
and public tests give the engine CI, history and the coverage report SonarQube needs. That spec's
section 7 assumed real fixtures were fine "because they are never committed", which no longer holds.

**Before publishing engine tests, which is hard to undo:**

- `test_finance_daily_dag` points at a real workspace and moves to the private repo. It is also the only
  link between the engine and the real `job.yaml`, hence the rule under "Selection across repos".
- Publish from an explicit list of engine test files, each reviewed, not from a count.
- `leakguard.py` blocks `tests/` as a path and its CPF pattern matches phone-number-shaped values in
  the Twilio test, so CI would fail. Replace the path block with the list and allowlist those values.
- `pytest.ini` is gitignored and `pyproject.toml` has no test configuration or dependencies. Both change.
- Pattern scanners cannot recognize a real financial value, so real-data tests never go in the
  public repo. That structure is the main protection; `gitleaks` and `leakguard` are the backstop.

**Before the first push of pipeline tests to the private repo:**

- Its `.gitignore` has an unanchored `report/`, which also ignores `scripts/report/`, so those 4 scripts
  are not versioned and tests importing them fail on a clone. Anchor it as `/report/` and commit them.
- **[owner]** Replace pinned real values with fake values. Tests that truly need real values stay on the
  owner's machine. This covers pinned anchors, CPF-shaped values in `test_raw2cleansed.py` not yet
  confirmed as fake, and real account numbers recorded in the migration spec (section 5.5).

`docs/`, repo-root `CLAUDE.md` and beads remain without off-machine backup. That is outside this design.

## Workspaces

**[owner]** ~~Development happens in `GetFinance-Dev`, with real data, and tests must run there.~~
**Amended 2026-11-12 (df-sgyf):** there is one GetFinance workspace, `GetFinance`. Development and
the daily collection happen there, and tests run against it; `GetFinance-Dev` is archived. Only
`data/raw` and `data/edicao.db` cannot be rebuilt: tests never write to them, and a weekly job backs
them up. Every "Dev" below now means `GetFinance`.
`GetFinance-Show` is a simple public example with fake data. It can be ignored during development
and filled in later, so pipeline tests do not depend on it. `DataForge-QA` is separate and stays
(`docs/qa-environment.md`).

Exceptions, because Show is still used: **[call]** df-dgq's first deliverable and its Playwright tests
run on Show, and the README's Show command should keep working. One public smoke test of that command
covers it.

Tests run in two tiers: **[call]**

| Tier | Code under test | Data | Checks |
|---|---|---|---|
| Deterministic | Dev scripts | Generated or owned by the tests, in `tmp_path` | Exact results and properties |
| Real data | Dev scripts | Dev real data, read-only, marked `integration` | Invariants, because the data changes whenever it is refreshed |

Real values never go into fixtures, recordings, beads text or reports. Today's tests do not match this
yet: `test_report_build` writes to Dev `data/bridge`, and two oracle tests read real paths with no
marker. They need a temporary output and the marker.

**Moving the tests.** (df-rt6w; since 2026-11-12 there is one checkout on `main`.) The private repo root is the workspace root, with `dev` and `prod` as separate
clones, so the harness cannot stay as it is: `_delta_util.py:11` builds a `workspaces/<name>` path that
will not exist. Drop `GETFINANCE_TEST_WORKSPACE`, resolve scripts relative to the checkout, and remove
the `_IS_DEV` branches and per-file hardcoded workspaces. Each checkout tests itself. **[call]**
**Done 2026-11-13 (df-rt6w):** GetFinance-Workspace PR #8 put the tests in that repo's `tests/`
(`_delta_util` resolves `ROOT` from the checkout and `REAL_DATA` from the main checkout via
`git --git-common-dir`; 13 real values replaced with fakes; the two oracle files are `integration`);
its CI runs leak-scan, the deterministic tier with coverage, and Sonar. DataForge PR #54 gives
`workspaces/*/tests/` the gate's tests/ allowances.

**Tests and deploy.** **Retired 2026-11-12 (df-sgyf):** there is no deploy between GetFinance
workspaces; the gate is the private repo's PR to `main` (df-rt6w). Original text, for history:
**[owner]** `deploy apply` requires passing tests, with an explicit option to
force. The commit to `prod` is part of deploy, because `dev` and `prod` share no history to merge. Deploy
copies only the pairs listed in `deploy/to-prod.yaml`, so a root `tests/` is never carried to prod.

**Engine version.** Private CI installs `DataForge` from the public repo at a pinned commit. **[call]**

**Real-run map.** **Amended 2026-11-12 (df-sgyf):** with one workspace, the real run is the daily
job itself or `reclassificar` (no Pluggy); no copy of prod is needed. Original text, for history:
**[owner]** A periodic Dev run on data copied from prod, with no Pluggy calls, so the
map measures the same code as the tests without instrumenting the production job. Dev's trigger is
disabled today and Dev and prod scripts differ, so this run must be added.

**Selection across repos.** Pipeline scripts do not import `dataforge`, so nothing links an engine change
to pipeline tests. A change to `parser`, `models`, `dag`, `engine` or `tasks` also runs the private
pipeline tests locally, because public CI cannot. **[call]**

**Rounds in each repo.** Public PR round: engine tests, leak scan, SonarQube gate. Private PR round:
pipeline deterministic tests. The scheduled round runs on the owner's machine. Neither checkout has git
hooks installed today, and the private repo has no CI or pre-commit config, so "cannot land" has no
enforcement point there yet.

## Constraints

- `docs/` and repo-root `CLAUDE.md` stay out of the public repo.
- Task scripts run in a subprocess (`dataforge/tasks/python_handler.py:57`). Coverage and test
  selection must be set up to see them.
- The test harness runs scripts with `exec(source, ns)` (`tests/workspaces/_delta_util.py`), and 16
  Dev scripts load helpers with `exec(open(...))`. Checked 2026-11-04: coverage does not record code
  run from a plain string, but does when compiled with its file name. Both must change: the harness
  must compile with the real path and keep line numbers, and the script loader too, or real runs will
  not record `functions.py`, where the high-risk rules are moving. A normal import is a poor fit: it
  breaks the harness's injection of `_connection_root` and flat scripts run only once. Prefer
  `compile()` or `runpy.run_path`, and confirm in a spike that mutation works with it. **[call]**
- Code runs on WSL and on Windows, and some branches are OS-specific. Maps must be mergeable.
- Existing conventions hold: paths via `dataforge.compat`, flat scripts, imports at the top of
  `functions.py`. Ask before adding a heavy dependency.
- Polars + deltalake only (no pandas/pyarrow in the dev venv). `polars.testing.parametric` is present in
  polars 1.41.2 and requires Hypothesis, which is not installed yet.

## Validation in this pipeline

Rules run before the curated write, so the report keeps yesterday's data. Two engine facts apply: a
failed non-leaf task with skipped leaves ends as `success` (`engine.py:128-131`), so the job needs an
`error` task on failure; and there is no warning state, so suspicious data alerts through a `message`
task. The future save action of the editing screens (df-cns) calls the same function. Crash sweep
targets: deploy apply/rollback, Delta upserts, job-run state.

## High-risk rules here

All four `raw2cleansed` scripts combine parquet files with `diagonal_relaxed`, then keep the latest row
per id (`sort("ingested_at", descending=True)` + `unique(subset=["id"], keep="first")`). One generic
function could cover them, and one property suite would then protect every caller. High-risk rules are
values, signs and accumulation.

## SonarQube measurement

Measured 2026-11-04 on the public code at `9cc4134` (72 Python files, 6,893 lines of code), with
local SonarQube Community 26.10, Ruff 0.17.3 (all rules) and Pylint 4.1.3. No published comparison
of the three tools on the same code was found. Limits: one codebase, no coverage or git history,
overlap approximated by "same line", findings not reviewed one by one, Cloud rules may differ.

| Tool | Findings |
|---|---|
| SonarQube, default profile (`Sonar way core`, 232 rules) | 20 |
| SonarQube, `Sonar way comprehensive` (407 rules) | 112: 0 bugs, 4 vulnerabilities, 0 hotspots |
| Pylint | 785 |
| Ruff, all rules | 2,033, mostly style |

59 of the 112 share a line with a non-style Ruff or Pylint finding. The other 53: API errors
undocumented in FastAPI (26), repeated strings (11), functions over the cognitive-complexity limit
(8, of 16 in total), nested conditionals (3), PRNG warnings in the synthetic generator, likely false
alarms (2), identical functions (1), small cleanups (2).

None of the rules SonarQube triggered cover what Ruff and Pylint found: `open()` without encoding
(25), network calls without timeout (5), `subprocess.run` without `check` (10), broad `except` (39).
Pylint also reported 24 duplications against SonarQube's 2 blocks.

Consequences for the build:

- **Roles.** Ruff and Pylint run in the fast round. SonarQube Cloud adds the PR gate, PR comments and
  history, as in the owner's CultHub project, which reports PR analysis at no cost. No server to maintain
  (the local server used 2.3 GB of RAM).
- **Profile.** Confirm the comprehensive profile can be enabled on Cloud. The default misses
  cognitive complexity.
- **Coverage.** The public CI runs the engine tests and produces `coverage.xml` for the gate.
- **Scope.** The public project sees `dataforge/` and Show, not the Dev scripts where pipeline work
  happens. Local Ruff and Pylint must run on Dev regardless. Show has no tests, so it is excluded
  from the coverage condition, or every Show change fails the gate.
- **Noise.** 56 "undefined name" reports come from scripts loading helpers with
  `exec(open(...functions.py))`. Resolve this so it does not hide real findings.

**Private repo.** The owner has no preference yet on whether SonarQube Cloud also analyzes it. **[call]**
Try it once on `scripts/` only (code, no data). Keep it if PR analysis works at no cost, which is
unverified for private repos. If it does not, drop it: local Ruff and Pylint already cover Dev.

## Relationship with df-cns and df-dgq

df-cns is a proposal still in discussion; df-dgq is in progress with an approved first deliverable.
df-cns says the Excel report is the main obstacle to testing because the logic that fails sits after
the ETL. Moving it into the ETL makes this design apply. **[call]**

- **First pipeline customer.** After the first slice, df-cns step 1 is the pipeline pilot: routing,
  installments, next-bill items and monthly aggregates.
- **One routing function, but independent tests.** Routing exists twice today (Power Query and
  `_route` in the oracle). Production gets one function. Tests must not import it as their own
  oracle, or they validate themselves: use properties and hand-calculated cases.
- **The 11 `_validacao` checks split.** df-cns turns them all into pytest. Rules about code become
  tests; rules about the day's data become pipeline validation, sharing the same function.
- **Validation has two entry points.** Editing screens reclassify without running the job, so the
  save action runs the same rules (duplicate subcategory, invalid Tipo, wrong-direction rule).
- **No test investment in Excel.** The workbook and bridge CSVs are planned to disappear. Validation
  protects curated tables and aggregates, not the workbook.
- **UI tests follow df-dgq.** The web front plans Playwright with screenshot comparison, so it is
  the UI candidate. Schemathesis gains value as read endpoints appear.
- **Pending there.** Where editable tables live (config or database) affects fixtures, generators
  and the declared dependencies of test selection.

## Starting point

Measured 2026-11-04. These matter once work goes beyond the first slice:

- The full non-integration run segfaulted twice; the second time was in a client startup at
  `tests/test_jobs_crud.py:118`, whose file passes alone. `2026-11-04-financas-v0.md` records that this
  happens when GUI tests run first. Cause not diagnosed. Whole-suite mutation, selection and public CI
  depend on solving it; running GUI tests separately is a known workaround.
- `test_finance_daily_dag` fails (`fact_fatura_mes` not expected) and reads the real workspace by
  default. That plan lists 4 more failures in pipeline tests, not reproduced here.
- No coverage gate exists in `pytest.ini` or `pyproject.toml`; `docs/testing.md` was corrected on
  2026-11-05.
- Nine tests marked `xfail` passed in the full run; those markers need review.
