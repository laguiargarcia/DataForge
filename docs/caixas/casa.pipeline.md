---
topic: "Pipeline GetFinance / Pipeline do módulo finanças"
read_when: "Working on the C4 box casaHoje.pipelineHoje, casaAlvo.pipelineAlvo or the code it covers"
code:
  - workspaces/GetFinance
status: esboço
---

# Pipeline GetFinance / Pipeline do módulo finanças

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje` | conteiner | Python, polars, deltalake; um processo por task, lançado pelo DataForge |
| `casaAlvo.pipelineAlvo` | conteiner | Python, polars, deltalake; um processo por task, lançado pelo DataForge |

`casaHoje.pipelineHoje`: As tasks dos jobs (finance_daily, extrato, reclassificar, backup) e a biblioteca de regras em scripts/report (edicao, classificacao, extrato), que o backend e o ninho importam

`casaAlvo.pipelineAlvo`: As tasks dos jobs (finance_daily, extrato, reclassificar, backup), cada uma um processo lançado pelo DataForge, e o pacote de regras de edição e classificação, que o servidor também importa. Único escritor do lago. Muda: sai de workspaces/GetFinance para ninho/financas, e as regras viram um pacote importável no lugar de scripts/report carregado por sys.path

Code: `workspaces/GetFinance`.
