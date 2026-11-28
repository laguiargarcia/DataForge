---
topic: "Agendamento"
read_when: "Working on the C4 box casaAlvo.dataforgeAlvo.agendamentoAlvo or the code it covers"
code: []
status: esboço
---

# Agendamento

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaAlvo.dataforgeAlvo.agendamentoAlvo` | componente | Python |

`casaAlvo.dataforgeAlvo.agendamentoAlvo`: O registro de cada trigger como tarefa no agendador do sistema, num módulo próprio que a CLI e a API usam; hoje são funções privadas de dataforge/cli.py, que a API importa (df-2kia)
