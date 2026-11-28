---
topic: Execution contract (how a product asks DataForge for a run, follows it, and where a project lives)
read_when: Changing `dataforge run`, the run history, the run lock or how a workspace path is resolved; building the part of ninho that asks for a job; adding a command a product may call
related_issues: [df-8x0m.23.2, df-8x0m.23]
transversal: true
---

# Execution contract: the engine's public interface

One of the contracts of `docs/adr/0006-architecture-v2-four-pieces.md`. It is the target, not
today's code. Today: `dataforge run <workspace> <job>` runs a job with no server up, waits for it to
end, prints text for a person and never prints the run id, exits 1 when the run fails or the
workspace is unknown, waits in a per-workspace queue (a file lock) when another run holds the
workspace, and finds the workspace only under `$DATAFORGE_HOME/workspaces/<name>` (a folder with a
`workspace.yaml`). A job that does not exist ends in a traceback and leaves a `pending` row in the
history that nothing finishes (df-8x0m.23.16). The OS scheduler calls `dataforge scheduled-run
<workspace> <trigger>`, which runs the trigger's jobs the same way.

## What crosses

A product talks to the engine through the command line, and only through it. No DataForge server
has to be up, and the product imports nothing from `dataforge`.

| The product needs to | The engine offers |
| --- | --- |
| Ask for a run | A command that takes a workspace name and a job name, starts the run and **answers at once with a run id**. It does not wait for the job to end, and the run is not tied to the caller's lifetime: it goes on if the caller exits or is killed. |
| Follow a run | A command that takes a run id and answers its state. The state lives in the engine's run history, so it survives a restart of whoever asked. |
| Read what went wrong | The same answer: on failure it names the task that failed and the reason. The run history keeps every run, so it is the error history too. |
| Find a project outside `workspaces/` | A registry in DataForge that maps a workspace name to a folder. Default: `workspaces/<name>`. Everything else keeps using the name only. A name whose folder is missing or has no `workspace.yaml` is an invalid request. |

**Answer format.** With the option for programs, each command prints one JSON object with fixed
fields: run id, workspace, job, state, start, end and, on failure, the failed task and the reason.
Without the option the output stays text for a person, as today.

**States.** A run is, in order: accepted and not started (which includes waiting in the queue),
running, then one end state (success or failed). The state command tells "waiting in the queue"
apart from "not started yet". A refused request never becomes a run in the history as a started
one; it is recorded as refused.

**Exit codes.** One per kind of outcome, so the caller can tell "the job ran and failed" from "it
never started": success; the job failed; refused because the workspace is busy; invalid request
(for example, a job that does not exist).

**Two requests at once.** The answer has two possible outcomes besides "started": **refused** and
**waiting in the queue**. Which one applies to which request is open (see Decision).

The busy check is the engine's file lock, which only sees processes of the same system: a run
started from WSL and one started on Windows do not see each other. Prod runs only on Windows.

**What a task gets.** Unchanged, as `docs/workspace-layout.md` describes it: a task is a script
started as a process; it receives its own `params` as `--key value` arguments; its environment is
the caller's plus `DATAFORGE_HOME` plus the workspace's `env_file`.

## Owners

- DataForge owns the commands, the answer format, the exit codes, the run history, the registry and
  the queue.
- The product owns its project folder (jobs and scripts) and decides when to ask for a run.
- Scheduling stays with the OS scheduler, registered through DataForge as today; a scheduled
  trigger is one more way a run is asked for, and gets the same history and the same states.

## Check

A contract test in the engine's tests, on a synthetic workspace, with no product code:

- asking for a run answers a run id before the job ends, in the JSON shape above;
- the state command answers each state a run goes through, and names the task and the reason on a
  failed run;
- each outcome gives its own exit code;
- a second request for a busy workspace gives one of the two declared outcomes, never a third;
- a workspace registered in another folder runs, and one that is not registered is found under
  `workspaces/<name>`;
- a field that is removed or renamed, or an exit code that changes, fails the test;
- the run reaches its end state after the process that asked for it is gone, and the state command
  still answers it;
- an invalid request (unknown job, registered folder missing) gives its exit code and leaves no
  unfinished run in the history;
- a task of the synthetic job sees its `params` as arguments and the `env_file` values in its
  environment.

The wall rule of ADR 0006 (the product does not import `dataforge`) will be a quality-gate rule; it
does not exist yet (df-8x0m.23.14).

**Covered by (walking skeleton, df-8x0m.23.13.6, 2026-11-24).** Tests in
`tests/test_cli_execution_contract.py` unless said otherwise:

- run id before the job ends, JSON shape:
  `test_o_pedido_responde_o_id_antes_de_o_job_terminar_e_a_execucao_segue_sozinha` (the fields are
  pinned in `FIELDS`, checked on the request's answer, the end state and `--wait`'s answer).
- each state, failed task and reason: `test_o_estado_passa_por_running_e_uma_varredura_de_orfaos_nao_derruba_a_execucao_viva`,
  `test_uma_execucao_que_falha_diz_a_tarefa_e_o_motivo`, `test_falha_diz_tarefa_e_motivo`;
  `run-state --wait`: `test_wait_so_responde_depois_do_fim_com_o_estado_final`,
  `test_wait_com_timeout_curto_responde_o_estado_em_andamento_e_sai_com_4`.
- exit codes, and a changed one fails: `test_os_codigos_de_saida_estao_fixados`.
- the run lives on after the requester is gone: the first test above, and
  `test_execucao_cujo_processo_morreu_termina_como_falha_ao_perguntar_o_estado`,
  `test_processo_que_nunca_assume_a_execucao_vira_falha_com_motivo`.
- invalid request leaves no unfinished run: `test_pedido_invalido_sai_com_2_e_nao_deixa_execucao_no_historico`,
  `test_job_com_yaml_invalido_e_pedido_invalido`, `test_estado_de_um_id_desconhecido_sai_com_2`.
- busy workspace: only the "waits" outcome exists (`test_com_o_workspace_ocupado_o_pedido_espera_na_fila_e_o_estado_diz`);
  "refused" is still to build (df-8x0m.23.15).
- a product asks and follows a run through the command line only, end to end:
  `tests/ninho/test_aplicar.py` (the server's "apply" against the real `dataforge` executable) and
  `tests/ninho/test_esqueleto.py` (the skeleton up as a process, read, correct, apply, read back).
- the wall: the `independence` contract of `dataforge` and `ninho` in `pyproject.toml`
  (import-linter, run by the quality gate) already refuses an import either way; it was built with
  the ninho package (df-8x0m.23.13.2), before df-8x0m.23.14.

Left for later: the registry of workspace folders (a workspace registered in another folder; today
only `workspaces/<name>` exists, and ninho takes the engine's home as two folders above its
workspace), "refused" for a busy workspace (df-8x0m.23.15), and a contract test that a task sees
its `params` and `env_file` values (today only the engine's general tests and `finance_demo` touch
them, not this contract's test).

## Left to the implementer

The names of the commands and of the option, the names and types of the JSON fields, the numbers of
the exit codes, the mechanism that keeps the run alive after the command answers, where the registry
is stored, and how often a caller asks for the state. Names and numbers are free to choose once:
after they are chosen they are the contract, and the test pins them.

## As built (df-8x0m.23.13.1, 2026-11-24)

- `dataforge run <workspace> <job> --detach` and `dataforge run-state <run_id>`; tests in
  `tests/test_cli_execution_contract.py`.
- The request answers after the detached process has taken the run over (about one interpreter
  start), not literally at once: until then the requester owns the row, so a sweep of orphaned runs
  cannot end a run that is about to start. A process that never takes over ends the run as failed.
- `run-state` ends a run whose process died before answering, so a caller never polls forever.
- Exit codes: 0 answered, 1 the job failed (the run that waits, or a detached run that already
  failed or never started when it answered), 2 invalid request or unknown id, 3 reserved for busy.
  A failed job seen through `run-state` is `state: failed` with exit 0.
- "Waiting in the queue" is stored as a marker in `job_runs.reason` of a `pending` row, cleared when
  the run starts; other readers of the history see that marker as the reason of a pending run.
- `run-state --wait [--timeout N]` answers once, when the run has ended (exit 4 when the time runs
  out first). Lucas chose it on 2026-11-24: starting the CLI costs about 1 s on Windows prod, so a
  caller follows a run with one process instead of one per question (df-8x0m.23.17).
- Not built yet: the registry of workspace folders, refuse-or-wait (df-8x0m.23.15).

## Decision

Lucas, 2026-11-24:

- Command line only.
- The caller must be able to ask for the state and never waits blind: the request answers a run id
  at once and a second command answers the state. How often to ask is the implementer's.
- A stable answer for programs and exit codes with a fixed meaning; he also wants the error history
  to be precise, for quality control. The shape (one JSON object with fixed fields, one exit code
  per kind of outcome) was proposed by Claude and he agreed ("ok").
- A project's folder comes from a registry in DataForge, defaulting to `workspaces/`.
- What a task gets does not change.
- **Open, on purpose:** which request is refused and which waits. His first position is the
  conservative one, refuse the second (whoever was refused fetches what the first run changed and
  only then tries again). Options he raised: a manual request is refused and a scheduled one waits;
  or it is a setting of the job or of the trigger. Today every request waits. A caution
  from Claude, not yet answered: if the daily scheduled job is refused because an "apply" runs at
  that moment, the day's load is lost. It does not block the contract, which only needs both
  outcomes to exist in the answer. Bead: df-8x0m.23.15.
