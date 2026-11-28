---
topic: "CLI / CLI (interface pública)"
read_when: "Working on the C4 box casaHoje.dataforgeHoje.cliHoje, casaAlvo.dataforgeAlvo.cliAlvo or the code it covers"
code:
  - dataforge/__main__.py
  - dataforge/cli.py
status: esboço
---

# CLI / CLI (interface pública)

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.dataforgeHoje.cliHoje` | componente | Python, Typer |
| `casaAlvo.dataforgeAlvo.cliAlvo` | componente | Python, Typer |

`casaHoje.dataforgeHoje.cliHoje`: O comando dataforge (Typer): run, run --detach, run-state, scheduled-run, serve, background, gui, app (o que o atalho roda), stop, deploy, register-triggers, entre outros (jobs, execucoes, validate, init, schedule-sync); registra cada trigger no agendador do sistema

`casaAlvo.dataforgeAlvo.cliAlvo`: O comando dataforge (Typer). A interface pública que o produto usa: run --detach e run-state --wait; scheduled-run, que o agendador do sistema chama; register-triggers; e os comandos de administração. Muda: acha o workspace pelo registro de pastas (df-7cyk); o registro no agendador vai para o Agendamento (df-2kia); saem os comandos que servem um app (app, o atalho), e o resto a decidir em df-8x0m.23.3

Code: `dataforge/__main__.py`, `dataforge/cli.py`.
