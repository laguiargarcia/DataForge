---
topic: Reading contract (the tables the pipeline publishes for the server to read)
read_when: Adding, renaming or changing a table or column a screen reads; changing how the server reads the lake; adding a per-person view of the data
related_issues: [df-8x0m.23.4, df-8x0m.23.6, df-8x0m.23]
---

# Reading contract: lake → house server

A starting plan, not a frozen spec (see `README.md`). Today the Finanças screens ask DataForge's
generic route for whole tables of the curated layer (`/api/workspaces/<ws>/tables/<table>`):
`fact_transaction_classified`, `report_saude`, `report_saude_categoria`, `report_cartao`,
`report_investimentos`, `report_liquidity`, `conferencia_saldo`, `lancamentos_suspeitos`. The
Finanças backend also reads curated tables straight from the Delta files: `fact_transaction`,
`edicao_aplicada` (the edits version the pipeline last used), `conferencia_saldo`, `dim_account`,
`extrato_saldos`. No schema is declared anywhere: a renamed column is found when a screen breaks.

## What crosses

- **Published tables.** The pipeline publishes a named list of tables for the server. The list
  starts as every table the screens and the backend read today (the two lists above). Any other table of the lake is internal and may
  change freely.
- **A declared schema.** Each published table has its columns and types written in one place, in
  the finance module.
- **Domain routes.** The server reads the published tables, read-only, and answers the screens
  through routes that say what they mean ("transactions of this month"), not by handing over a
  whole table by name.

## Owners

- The pipeline is the only writer of the lake and owns the published list and its schema.
- The server only reads. It never writes to the lake and never reads an internal table.
- A schema change is made in the declaration, the pipeline and the server's route in the same change.

## Check

- Each job that writes a published table (the daily job and "apply") ends with a task that
  compares the tables with their declared schema and fails the run when they differ.
- A test of the server reads every published table of the synthetic run through its routes.
- A quality-gate rule, still to be written, refuses a server route that reads a table outside the
  published list.

**Covered by (walking skeleton, df-8x0m.23.13.6, 2026-11-24).** Only one table is read so far,
`fact_transaction_classified`, by one route (`GET /api/financas/transacoes?mes=AAAA-MM`), plus
`edicao_aplicada` for the version applied. Tests in `tests/ninho/test_leitura.py` unless said otherwise:

- the server reads the table of the synthetic run through its route: `test_transacoes_do_mes_com_a_senha`,
  `test_transacoes_vem_da_mais_recente_para_a_mais_antiga`, `test_mes_sem_transacao_devolve_lista_vazia`,
  `test_sem_a_tabela_publicada_responde_503`; with the server as a process over HTTP,
  `tests/ninho/test_esqueleto.py`.
- the server only reads: `test_a_leitura_nao_escreve_no_lago`.
- a column the route hands out that the pipeline stops writing fails the read, not a smaller answer
  (`COLUNAS` in `ninho/financas/leitura.py`); no test pins it by name yet.

Left for later: the declared schema and the task that checks it at the end of each job that writes
a published table (neither exists: there is no published list yet, only the one table the route
reads); a route, and its test, for every other table of the list; the gate rule.

## Left to the implementer

The format of the schema declaration, whether the server reads the Delta files directly or a copy
made for it, the routes and their shapes, caching.

## Decision

Lucas, 2026-11-23: the server only reads. Part of the plan, not yet his decision: the product's
data stops going through DataForge's generic tables route (whether that route leaves the engine is
`engine-scope.md`, df-8x0m.23.3). **Open:** who sees what ("what is shared plus what is hers", df-8x0m.23.6). Until
Lucas tells the rule, every logged-in person sees everything; the mark of owner or shared enters
this contract when the rule is known.
