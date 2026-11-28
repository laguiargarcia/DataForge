---
topic: "validation"
read_when: "Working on the C4 box casaHoje.pipelineHoje.validationHoje or the code it covers"
code:
  - workspaces/GetFinance/scripts/validation
status: esboço
---

# validation

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.validationHoje` | componente | Python |

`casaHoje.pipelineHoje.validationHoje`: Task data_warnings: lê os achados suspeitos que as validações da rodada gravaram e sai 1 quando há algum: a rodada fica failed com os dados gravados (o alert_warnings, que os citaria, está inativo)

Code: `workspaces/GetFinance/scripts/validation`.
