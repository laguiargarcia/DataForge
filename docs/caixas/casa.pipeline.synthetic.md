---
topic: "synthetic"
read_when: "Working on the C4 box casaHoje.pipelineHoje.syntheticHoje or the code it covers"
code:
  - workspaces/GetFinance/scripts/synthetic
status: esboço
---

# synthetic

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.syntheticHoje` | componente | Python |

`casaHoje.pipelineHoje.syntheticHoje`: Task fetch do finance_demo, que roda só no dev e no CI, nunca na prod: gera dados sintéticos no formato do Pluggy e grava no raw, sem credencial

Code: `workspaces/GetFinance/scripts/synthetic`.
