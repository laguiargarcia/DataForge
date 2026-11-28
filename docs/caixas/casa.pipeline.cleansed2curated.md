---
topic: "cleansed2curated"
read_when: "Working on the C4 box casaHoje.pipelineHoje.cleansed2curatedHoje, casaAlvo.pipelineAlvo.cleansed2curatedAlvo or the code it covers"
code:
  - workspaces/GetFinance/scripts/cleansed2curated
status: esboço
---

# cleansed2curated

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.cleansed2curatedHoje` | componente | Python, polars, deltalake, pypdf |
| `casaAlvo.pipelineAlvo.cleansed2curatedAlvo` | componente | Python, polars, deltalake, pypdf |

`casaHoje.pipelineHoje.cleansed2curatedHoje`: Tasks das dimensões e fatos do curated (contas, calendário, investimentos, saldos, faturas, contrapartes) e das conferências (saldo, fatura, lançamentos suspeitos, extrato e veredito); contas e investimentos usam o config/

`casaAlvo.pipelineAlvo.cleansed2curatedAlvo`: Tasks das dimensões e fatos do curated (contas, calendário, investimentos, saldos, faturas, contrapartes) e das conferências (saldo, fatura, lançamentos suspeitos, extrato e veredito); contas e investimentos usam o config/

Code: `workspaces/GetFinance/scripts/cleansed2curated`.
