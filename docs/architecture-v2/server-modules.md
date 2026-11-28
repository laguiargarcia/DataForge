---
topic: Server-modules contract (what a module of the ninho house server is, what all modules share, how list and calendar are made)
read_when: Adding a module to ninho (list, calendar, a fourth one), moving the finance routes, deciding where a module's tables, routes or screen go, or asking whether list and calendar are built or adopted
related_issues: [df-8x0m.23.7, df-8x0m.23.20, df-8x0m.23.21, df-8x0m.23]
code:
  - ninho
status: pronto
---

# Server-modules contract: house server ↔ its modules

A starting plan, not a frozen spec (see `README.md`). Today ninho has one module, finances, whose
routes sit in the server file itself (`ninho/servidor/app.py`) and whose edits live in the finance
workspace's `data/edicao.db`. The shopping list is the first module that is not finances
(df-8x0m.23.20); the calendar follows in the same mould (df-8x0m.23.21).

## What crosses

A module is one folder, `ninho/<modulo>/`, and gives the server three things:

| Thing | Shape |
| --- | --- |
| Routes | One FastAPI `APIRouter`, every path under `/api/<modulo>/`. The server mounts it and puts the password in front; the module never checks who is calling. |
| Tables | Its own tables in the app database, named with the module's prefix. The module creates them on first use. No module reads or writes another module's tables. |
| Screen | One folder in the front, `ninho/telas/src/<modulo>/`, and one entry in the list of modules that the bottom bar shows. |

What the server gives every module: the login and the check that the person has the module, the
person who is calling (the name is shown as "who added it"), the connection to the app
database, the people of the house (id and name, for a module that marks data as someone's:
`people-and-visibility.md`), and the built screen.

The app database of ninho is one SQLite file, named by the environment variable `NINHO_BANCO`. The
finance edits stay in the workspace's `data/edicao.db` until the workspace moves into
`ninho/financas` (df-8x0m.23.14); every other module uses `NINHO_BANCO`.

Only finances uses the lake and asks the engine for a job. A module that needs neither (the list)
has no pipeline at all.

## Owners

- The server owns login, people, the app database file and the screen's shell (the navigation).
- Each module owns its tables, its routes and its screen folder, and nothing outside them.
- Nobody but the server process writes to `NINHO_BANCO`.

## Check

- `lint-imports` (`pyproject.toml`): ninho does not import dataforge. A rule that one module does
  not import another is added when the second Python module exists (the list): an import-linter
  `independence` contract over `ninho.financas`, `ninho.lista`, later `ninho.agenda`.
- Each module has its route tests in `tests/ninho/test_<modulo>.py`, including "401 without the
  password" for every route.
- The browser test (`ninho/telas/e2e`) opens each module from the bottom bar.

## Left to the implementer

The schema of a module's tables, the names inside its folder, how the front keeps which module is
open, and whether the rules of a module sit in one file or several. Moving the finance routes out of
`app.py` into their own router is free to happen whenever finances is next touched.

## Decision

Lucas, 2026-11-24:

- **Built by us, inside ninho.** List and calendar are our own modules (tables, routes, screen, same
  login), not KitchenOwl or Grocy beside it: that is the only way they are really "in the same app".
- **List first, calendar after**, in the same mould.
- **List and calendar are fully shared.** No private item or event. The rule "what is shared plus
  what is hers" (df-8x0m.23.6) is a finance question only and no longer blocks this contract.
- **The list starts thin:** add an item, mark it bought, delete it, clear the bought ones.
- **The calendar starts inside the app only**, without Google (2026-11-23).
- The one shared password stays for now; the name typed at login is only a label.

Still open: what the thin calendar has (df-8x0m.23.21), and working without a network
(`mobile-client.md`, df-8x0m.23.8: list and calendar are the first candidates for the phone's queue).
