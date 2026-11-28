---
topic: "Tipos de task"
read_when: "Working on the C4 box casaHoje.dataforgeHoje.tasksHoje, casaAlvo.dataforgeAlvo.tasksAlvo or the code it covers"
code:
  - dataforge/__init__.py
  - dataforge/tasks
status: esboço
---

# Tipos de task

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.dataforgeHoje.tasksHoje` | componente | Python |
| `casaAlvo.dataforgeAlvo.tasksAlvo` | componente | Python |

`casaHoje.dataforgeHoje.tasksHoje`: O registro dos tipos de task e os embutidos: python (o script do workspace num processo), job, erro e mensagem (e-mail, SMS, WhatsApp; desligada nos jobs de hoje: finance_daily/job.yaml active false). O __init__ do pacote registra os embutidos em todo processo que importa dataforge, o backend do app inclusive

`casaAlvo.dataforgeAlvo.tasksAlvo`: O registro dos tipos de task e os embutidos: python (o script do workspace num processo), job, erro e mensagem (e-mail, SMS, WhatsApp; desligada nos jobs de hoje: finance_daily/job.yaml active false). O __init__ do pacote registra os embutidos em todo processo que importa dataforge. Muda: quem é avisado quando o pipeline falha ou o dado diverge, a decidir em df-8x0m.23.9

Code: `dataforge/__init__.py`, `dataforge/tasks`.
