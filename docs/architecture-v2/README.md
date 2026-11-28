---
topic: Architecture v2 contracts (what the four pieces agree on with each other)
read_when: Building or changing anything that crosses a boundary between the house server (ninho), the app database, the lake and DataForge; writing one of the contracts; asking what is still undecided in the v2 design
related_issues: [df-8x0m, df-8x0m.23]
---

# Architecture v2 contracts

`docs/adr/0006-architecture-v2-four-pieces.md` says what the four pieces are and which way the
dependencies point. This folder holds one file per boundary between them. A contract says what two
pieces agree on, not how either is built inside: whoever builds a piece is free in everything the
contract does not name.

## How to read them

A contract is the starting plan for a boundary, not a frozen spec (Lucas, 2026-11-24: no waterfall;
changes will come, but there has to be an initial plan). It says in plain words what the two sides
agree on today. When building shows a better way, change the contract in the same change and say
why in its `## Decision` section. A contract is written when its piece is about to be built, not
all of them up front.

## Format of a contract

Front matter like any doc (`topic`, `read_when`, `related_issues`), then five sections:

| Section | Says |
| --- | --- |
| `## What crosses` | The data or the call that goes over the boundary, and its shape. |
| `## Owners` | Who owns each side, and who may write. |
| `## Check` | The test or the quality-gate rule that fails when the contract is broken. A contract without one is not done. |
| `## Left to the implementer` | What is free on purpose. |
| `## Decision` | What Lucas decided, with the date. |

## The contracts

A file exists once its contract is written; a missing file means its bead holds what is known.

| File | Question it answers | Bead | Waits for |
| --- | --- | --- | --- |
| `engine-interface.md` | How does a product ask the engine for a run, follow it, and where may a project live? | df-8x0m.23.2 | |
| `engine-scope.md` | What leaves DataForge, where does each part go, and what do the admin screen and API become? | df-8x0m.23.3 | |
| `published-tables.md` | Which tables does the pipeline publish to the server, where is their schema declared, and who checks it? | df-8x0m.23.4 | (the visibility rule enters later, df-8x0m.23.6) |
| `edits.md` | What is the app database, how does the pipeline read the finance edits, and where do the business rules live? | df-8x0m.23.5 | |
| `people-and-visibility.md` | Who logs in, how, and what does "what is shared plus what is hers" mean in the data? | df-8x0m.23.6 | (the rule inside finances still waits for Lucas) |
| `server-modules.md` | What is a module of the server, what is common to all, and are list and calendar built, adapted or embedded? | df-8x0m.23.7 | |
| `mobile-client.md` | Is there one front for phone and PC, what works without a network, and how does the Android phone join the private network? | df-8x0m.23.8 | |
| `operations.md` | How does the server stay on, how does a deploy stop and start it, how do dev and prod differ, where does the backup go off this disk, and who is told when the pipeline fails? | df-8x0m.23.9 | |
| `raw-and-sources.md` | How does raw only grow, what is a source, and how does a second one come in? | df-8x0m.23.10 | |
| `migration-order.md` | Which steps lead from today's code to v2, with prod working after each one? | df-8x0m.23.11 | df-8x0m.23.2, .3, .4, .5, .9 |

Three beads of the same set produce beads, not a file: the walking skeleton that crosses the four
pieces (df-8x0m.23.13, after df-8x0m.23.2, .4 and .5), the triage of the open beads that v2 changes
(df-8x0m.23.12, after df-8x0m.23.3) and the implementation of the repository walls (df-8x0m.23.14,
after df-8x0m.23.11).

## First test on a phone (df-8x0m.23.13.7, 2026-11-24)

The skeleton's screen was opened on Lucas's iPhone, over the private network, against prod: he listed
a month, corrected a category and applied, and all three worked. What it took, for the two contracts
not written yet (`operations.md`, df-8x0m.23.9; `mobile-client.md`, df-8x0m.23.8):

- **Where it ran.** On Windows, from prod's own install (`.venv-prod`, `python -m uvicorn
  ninho.servidor.app:app --host 127.0.0.1 --port 8100`, `NINHO_WORKSPACE` = the prod workspace). Lucas
  chose prod over WSL or a test folder: the server will live there, and a hop through WSL would not
  exist in the product.
- **How the phone reached it.** `tailscale serve --bg 8100` on Windows: HTTPS on the machine's
  tailnet name, tailnet only, proxied to the loopback port. The server never listens on the network,
  so the Windows firewall asks nothing. Serve has to be enabled once for the tailnet, by the account
  owner, on a link the command prints.
- **What the wheel must carry.** Prod installs a wheel and has no checkout: the built screen
  (`ninho/telas/dist`) had to become package data (df-8x0m.23.13.11).
- **What operations still has to answer.** The server was started by hand and does not survive a
  reboot; while it runs from `.venv-prod`, a deploy stops at "prod is busy"; one shared password
  stands in for login (df-8x0m.23.6).
- **What the client still has to answer.** The screen is a small part of today's Finanças app; the
  gap is listed in df-8x0m.23.19. Only Chromium at iPhone size runs in the browser test (df-dffr).

## The list in prod (df-8x0m.23.20.4, 2026-11-24)

The shopping list, the first module that is not finances, was deployed and served from prod the same
way as the first phone test. What it added, for `operations.md` (df-8x0m.23.9):

- **Where the app database lives.** `NINHO_BANCO` = `<WIN>\ninho-dados\ninho.db`: a folder beside
  `workspaces/` and `.venv-prod`, which no deploy writes, mirrors or deletes. Nothing backs it up yet.
- **A deploy has to stop the ninho server first.** It runs from `.venv-prod`, so `update-prod`
  refuses with "prod is busy"; `dataforge stop` does not know it. It was stopped by process id and
  started again by hand after the deploy, with a new password (the environment of the old process is
  lost with it).
- **Checked on the Windows server after the deploy:** the page, `/api/lista/itens` and the finance
  routes answer 200 with the password and 401 without; `/docs` and `/openapi.json` are gone; one
  item added and deleted.

## People in prod (df-8x0m.23.6.4, 2026-11-24)

Login per person went to prod (`people-and-visibility.md`). What it took, for `operations.md`:

- **A deploy that changes login locks everyone out until the people exist.** The table `pessoas` of
  prod was empty: deploy, then create the people at once.
- **People are created by Lucas at a real Windows console**, never from WSL: the command refuses to
  ask for a password when its input is not a terminal. `$env:NINHO_BANCO` set to
  `<WIN>\ninho-dados\ninho.db`, then `<WIN>\.venv-prod\Scripts\python.exe -m ninho pessoas criar
  <name> --modulos financas,lista` (hers: `--modulos lista`); `... pessoas senha <name>` changes a
  password. No session ever sees a password.
- **The server is started without `NINHO_SENHA`** (it no longer exists) and needs no restart when a
  person is created or a password changed: it reads the table on every request.
- **Checked from Windows:** 401 on every route without a login; the command lists two people, one
  with finances and the list, one with the list only. The two logins in the browser were checked by
  Lucas.

