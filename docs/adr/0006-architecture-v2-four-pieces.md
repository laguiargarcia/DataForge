# Architecture v2: four pieces with narrow contracts; DataForge is the orchestrator, not a server

**Status:** accepted (Lucas, 2026-11-24, after reading this text; the sketch on 2026-11-23).
Decision: bead df-8x0m. This ADR and the
contracts: df-8x0m.23. Nothing here is built yet: the code still has the shape described under
"Where we are", and the path from one to the other is its own contract
(`docs/architecture-v2/migration-order.md`, df-8x0m.23.11).

## Where we are

DataForge started as a generic, Databricks-like orchestrator. When the GetFinance report left Excel
for a web app (df-cns), the app's server was given to DataForge (df-dgq, df-1mdh.1), and it also became the application platform of its only real
workspace: it hosts the Finanças app, starts and stops the app's backend, guards it with a token,
serves every table the screen reads through a generic route, and keeps a background process alive
for it. GetFinance, in turn, is a product that took the shape of a pipeline: its backend imports
DataForge internals and its business rules live in flat scripts.

The 22 pains this produced are beads (`bd list --label dor-arquitetura`), each traced to the
decision behind it (notes of df-8x0m). What forces the review now is a new axis of growth: an app
for the house, used by two people from their phones, with finances, a shared shopping list and a
shared calendar, served by this PC (df-b4d2).

## Decision

Split by kind of thing: what stays on, what runs and ends, what people type, what is computed.

| # | Piece | What it is | What it never does |
| --- | --- | --- | --- |
| 1 | **House server** | The only process that is always on and the only one that knows people and login. Modules: finances, shopping list, calendar. Serves phones and the PC the same way. | Run a pipeline itself; write to the lake. |
| 2 | **App database** | Everything a person typed: users, list, calendar, the finance edits (categories, rules, corrections). The data that cannot be rebuilt, so the one that needs a real backup. | Hold anything the pipeline can compute again. |
| 3 | **Lake** | The pipeline's tables, stored as Delta files: raw, cleansed, and published (the tables made for the server to read; today's curated layer). Raw only grows; the rest is rebuilt from it. | Take a write from the server. |
| 4 | **DataForge** | A generic orchestrator and nothing else: engine, scheduler, run history, CLI, an admin screen. It runs and ends. | Serve product data, host an app, know a user. |

The product is called **ninho**: the house server, its screens and its three modules. GetFinance
becomes ninho's finance module, in three parts: the pipeline (jobs that DataForge runs), a package
with the business rules, and the routes of its screens. The name GetFinance stays as the module's
name.

```
  her phone            Lucas's phone                the PC's browser
       └──────────────────────┬───────────────────────────┘
                              │ private network
    ┌─────────────────────────▼────────────────────────┐
    │ 1. HOUSE SERVER   always on, with login          │
    │    modules: finances · list · calendar           │
    └────┬──────────────────┬─────────────────────┬────┘
         │ reads, writes    │ reads only          │ asks: "run job X"
┌────────▼───────┐  ┌───────▼────────┐  ┌─────────▼────────┐
│ 2. APP DATABASE│  │ 3. LAKE (Delta)│  │ 4. DATAFORGE     │
│ what is typed: │  │ raw, cleansed, │  │ engine, schedule,│◄── bank provider,
│ users, list,   │  │ published      │  │ history, CLI,    │    other sources
│ calendar, edits│  │                │  │ admin screen     │
└────────┬───────┘  └───────▲────────┘  └────┬────▲────────┘
         │                  └──── writes ────┘    │
         └──── edits go in as pipeline input ─────┘
```

Without a network, the phone keeps working on its own: it holds a local copy of what it shows and a
queue of the changes made meanwhile, and sends the queue to the server when the network is back. The
server is still the only writer of the app database; the queue is how a write waits for it
(`docs/architecture-v2/mobile-client.md`, df-8x0m.23.8).

### Who owns each piece of data

| Data | Written by | Read by |
| --- | --- | --- |
| App database | the house server, and nobody else | the server; the pipeline, read-only, takes the finance edits as input |
| Lake | the pipeline (tasks that DataForge runs), and nobody else | the pipeline; the server, read-only, through the published tables |
| Run history and schedules | DataForge | the admin screen; the server, to follow a run it asked for (provisional: the engine-interface contract decides how) |

### Which way the dependencies point

- The product uses the engine **only through the engine's public interface**
  (`docs/architecture-v2/engine-interface.md`).
- The engine **never imports anything of the product**, and has no rule, route or job that exists
  for one product only.
- The server asks for a run through the CLI (or a library call, if the engine-interface contract
  offers one). No DataForge server has to be up: it is
  what the Windows Task Scheduler already does for the daily job (`dataforge run <workspace> <job>`).
- An edit is saved in the app database, and "apply" (today the `reclassificar` job, which redoes the
  categories and the numbers after an edit) stays a job asked of the engine.

### One public repository, with walls inside

```
dataforge/      the engine (generic orchestrator), its own package and version
ninho/          the product, its own package and version
   financas/    the finance module: pipeline, rules, routes (today workspaces/GetFinance)
   lista/       the shopping-list module
   agenda/      the calendar module
   ...          the server and the screens (folder names left to the implementation)
```

The folder is `financas`; "GetFinance" stays as the module's name in beads and docs. Data (the lake,
the app database, local config, keys) stays out of the repository, as today.

A wall is four things: (1) engine and product in separate folders, each with its own package and
version; (2) a quality-gate rule that lets the product use only the engine's public interface;
(3) CI runs only the jobs of the part that changed; (4) the engine never imports the product. With
the walls in place, moving the product to another repository later stays possible.

### What Lucas decided on 2026-11-23

- DataForge stays a generic, Databricks-like orchestrator. What goes away is its role as a server.
- Excel leaves every design. The screens are the only report.
- One public repository. The code of the house app is public too; data, names and keys stay out,
  as today.
- Prod stays on native Windows and the Windows Task Scheduler keeps firing the jobs. The Linux here
  is WSL on the same PC and cannot be counted on to be always up.
- An edit keeps going through the "apply" job.
- The DataForge UI may shrink or be paused for a while, but the idea of a UI for DataForge stays;
  it is an admin screen and has to match what runs behind it.
- List and calendar live inside the same app as finances. Open-source code, or embedding, is
  acceptable. The calendar may start inside the app only, without Google.
- She sees what is shared plus what is hers. The exact rule is still to be told (df-8x0m.23.6).

## Alternatives measured and dropped

| Alternative | Why it was dropped |
| --- | --- |
| Product in a private repository | The hand-kept example copy of the workspace would come back (Lucas: it goes stale fast; df-bsb2.2 removed it on 2026-11-20). CI stops being free: 527 runs in 30 days at about US$ 0.35 each (GitHub's price list read on 2026-11-23, 38.9 Linux and 8.8 Windows minutes per run in a sample of 37) is about US$ 180 a month, against US$ 12 included. A change that crosses engine and product would need two pull requests in order. |
| Prod on Linux (WSL) | The always-on server and the trigger would depend on a WSL that is not guaranteed to be up. Excel is no longer a reason to stay on Windows, but the scheduler and the always-on process are. The design does not depend on the OS. |
| Show an edit at once, joining edits and curated tables at read time | The wait it would remove is not a pain: "apply" (`reclassificar`) takes 2.9 s on average in prod (22 runs, 1.0 to 12.0 s). It would put the same classification rule in two places. |

## Consequences

- Several modules leave `dataforge/` or freeze. Candidates: the app host, the generic tables route,
  the deploy ledger, most of the Qt GUI. Which, and where each goes, is decided in
  `docs/architecture-v2/engine-scope.md`.
- Being generic gets cheaper (the engine shrinks to the orchestrator) and may get a second real use:
  ninho's own jobs beyond the finance pipeline, such as backup or calendar sync.
- The leak guard stays, because the repository stays public.
- The Windows-only class of bugs stays, covered by the Windows CI jobs.
- With an always-on server on Windows, "prod is busy" becomes the normal state: every deploy
  includes stopping and starting the server (`docs/architecture-v2/operations.md`).
- Not addressed here: the weight of the working process (pain D20) and a redesign of what a task is
  (pain D17, beyond what the engine's public interface has to promise).

## What is still open

Each boundary of the drawing is a contract, written before the pieces are built so that whoever
builds one piece is free inside it and cannot break the neighbour. The list, the format and the
bead of each one are in `docs/architecture-v2/README.md`. The first code is a walking skeleton: the
thinnest slice that crosses the four pieces on synthetic data (df-8x0m.23.13).
