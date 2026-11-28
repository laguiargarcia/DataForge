---
topic: "landing"
read_when: "Working on the C4 box casaHoje.pipelineHoje.landingHoje, casaAlvo.pipelineAlvo.landingAlvo or the code it covers"
code:
  - workspaces/GetFinance/scripts/landing
status: esboço
---

# landing

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.landingHoje` | componente | Python |
| `casaAlvo.pipelineAlvo.landingAlvo` | componente | Python |

`casaHoje.pipelineHoje.landingHoje`: Task fetch do finance_daily: busca contas, transações, faturas e investimentos no Pluggy e grava a partição do dia no raw

`casaAlvo.pipelineAlvo.landingAlvo`: Task fetch do finance_daily: busca contas, transações, faturas e investimentos no Pluggy e grava a partição do dia no raw. Muda: o raw só cresce (hoje cada busca regrava a partição do dia) e uma segunda fonte entra pelo mesmo lugar (df-8x0m.23.10)

Code: `workspaces/GetFinance/scripts/landing`.
