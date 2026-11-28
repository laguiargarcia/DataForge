---
topic: "functions"
read_when: "Working on the C4 box casaHoje.pipelineHoje.functionsHoje or the code it covers"
code:
  - workspaces/GetFinance/scripts/functions
status: esboço
---

# functions

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.functionsHoje` | componente | Python, requests, polars, deltalake |

`casaHoje.pipelineHoje.functionsHoje`: Biblioteca que toda task carrega por exec: cliente HTTP do Pluggy (com o .env), conexões do connections.yaml, leitura e gravação do raw (parquet) e do Delta, regras de validação e o subprocesso do reprocess

Code: `workspaces/GetFinance/scripts/functions`.
