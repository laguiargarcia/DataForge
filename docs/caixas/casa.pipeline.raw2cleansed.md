---
topic: "raw2cleansed"
read_when: "Working on the C4 box casaHoje.pipelineHoje.raw2cleansedHoje or the code it covers"
code:
  - workspaces/GetFinance/scripts/raw2cleansed
status: esboço
---

# raw2cleansed

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.raw2cleansedHoje` | componente | Python, polars, deltalake |

`casaHoje.pipelineHoje.raw2cleansedHoje`: Tasks transform*: limpam e deduplicam o raw de contas, faturas, investimentos e transações para o cleansed; o mapa de estabelecimentos vem de config/

Code: `workspaces/GetFinance/scripts/raw2cleansed`.
