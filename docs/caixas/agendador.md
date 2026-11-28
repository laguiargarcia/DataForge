---
topic: "Agendador do Windows"
read_when: "Working on the C4 box agendadorHoje, agendadorAlvo or the code it covers"
code: []
status: esboço
---

# Agendador do Windows

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `agendadorHoje` | sistemaExterno |  |
| `agendadorAlvo` | sistemaExterno |  |

`agendadorHoje`: Dispara o job diário e o backup (dataforge scheduled-run)

`agendadorAlvo`: Dispara os jobs (dataforge scheduled-run)
