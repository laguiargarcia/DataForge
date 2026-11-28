---
topic: Edits contract (the app database, how the pipeline reads what people edited, where the business rules live)
read_when: Changing where finance edits are stored, how a pipeline script reads them, the "apply" flow, or where classification and editing rules are imported from
related_issues: [df-8x0m.23.5, df-8x0m.23]
code: []
status: pronto
---

# Edits contract: app database → pipeline

A starting plan, not a frozen spec (see `README.md`). Today the finance edits live in
`data/edicao.db` (SQLite): categories, rules that map a text pattern to a category, per-transaction
corrections, explanations of balance differences, and a version number. The Finanças backend writes
it; the pipeline scripts (`classify`, the balance check, the backup) open the same file directly;
the backend imports the rules by adding `scripts/report` to `sys.path`. The pipeline also writes
there today: every open creates the file and its tables when missing, and a pipeline task
(`carga_inicial_edicao`) seeds them.

## What crosses

- **The edits.** The pipeline reads the finance edits from the app database and treats them as one
  more input, like raw data. It only reads: creating the tables and seeding them move to the
  server (or to a one-off step of the server), and the pipeline gets an open that cannot write.
- **The version number.** Every write to the edits adds 1 to it. The pipeline records which version
  it used, and the screen compares the two to say "there are edits not applied yet". This already
  works today and stays.
- **"Apply".** After editing, the server asks the engine for the job that redoes the categories and
  the numbers (`engine-interface.md`). Nothing else triggers it.
- **The rules.** Classification and editing rules live in one importable package inside the
  finance module of ninho (the house app, ADR 0006). The pipeline scripts and the server both import it; neither keeps a copy.

## Owners

- The house server is the only writer of the app database.
- The pipeline is a read-only reader of the finance edits and never writes there.
- The finance module owns the rules package and the shape of the edits tables.

## Check

- A test that the pipeline opens the edits read-only (a write attempt from a pipeline script fails).
- A test that one edit, then "apply", changes the published table and brings the two version
  numbers together.
- A quality-gate rule, still to be written, refuses `sys.path` tricks to reach the rules: they are
  imported as a package.

**Covered by (walking skeleton, df-8x0m.23.13.6, 2026-11-24).** Tests in `tests/ninho/test_edicoes.py`
unless said otherwise:

- the pipeline opens the edits read-only: `test_a_abertura_do_pipeline_nao_consegue_escrever`,
  `test_a_abertura_do_pipeline_sem_arquivo_le_vazio_e_nao_cria_nada`, and
  `test_os_scripts_do_pipeline_abrem_o_banco_de_edicoes_so_para_leitura` (each pipeline script that
  reads the edits goes through `abrir_leitura`).
- one edit, then "apply", changes the published table and brings the two versions together:
  `test_uma_correcao_e_gravada_soma_1_na_versao_e_o_aplicar_a_publica`; through the server's own
  "apply" route, `tests/ninho/test_aplicar.py::test_aplicar_devolve_um_id_o_andamento_chega_a_sucesso_e_a_categoria_nova_aparece`;
  with the server as a process over HTTP, `tests/ninho/test_esqueleto.py`.
- only the server writes, and never into a folder that is not a workspace:
  `test_pasta_que_nao_e_um_workspace_nao_ganha_banco`, `test_o_banco_do_app_e_o_arquivo_da_conexao_edicao_do_workspace`.

Left for later: the `sys.path` rule (the rules are still loaded from the workspace folder,
`ninho/financas/regras.py`, until the workspace moves into `ninho/financas`, df-8x0m.23.14); the seed
of categories and rules is still written by the pipeline (`carga_inicial_edicao`) and today's
Finanças backend still writes the same file, so "one writer" holds only for the corrections.

## Left to the implementer

Which database engine the app database is (SQLite with a single writer is enough to start), whether
the pipeline reads it directly or from a copy, the table and column names, how today's `edicao.db`
is carried over, and the package's name.

## As built (df-8x0m.23.13.3, 2026-11-24)

- The app database is, for now, the SQLite file of the workspace's `edicao` connection
  (`data/edicao.db`): the server (`ninho/banco.py`) creates it and its tables; the pipeline opens it
  with `edicao.abrir_leitura`, which cannot write (and reads an empty in-memory database when the
  file does not exist yet, creating nothing).
- Routes of the server: `GET /api/financas/edicoes` (corrections, categories, version, version
  applied, `pendente`), `PUT` and `DELETE /api/financas/correcoes/<id>`. Only corrections so far.
- The rules have one copy, the workspace's `scripts/report/edicao.py`, which ninho loads from the
  workspace folder (`ninho/financas/regras.py`). It becomes an importable package when the workspace
  moves into `ninho/financas` (df-8x0m.23.14).
- Still written by the pipeline, against this plan: the seed of categories and rules
  (`carga_inicial_edicao`, a task of `finance_demo`). Today's Finanças backend also still writes the
  same file. Tests: `tests/ninho/test_edicoes.py`.

## Decision

Lucas, 2026-11-23 and 24: only the server writes what people type; an edit keeps going through the
"apply" job (about 3 s in prod), not merged at read time; the plan can change as the walking
skeleton shows what works. Not decided yet: nothing that blocks the skeleton.
