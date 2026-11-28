---
topic: "report"
read_when: "Working on the C4 box casaHoje.pipelineHoje.reportHoje, casaAlvo.pipelineAlvo.reportAlvo or the code it covers"
code:
  - workspaces/GetFinance/scripts/report
status: esboço
---

# report

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.reportHoje` | componente | Python, polars, deltalake |
| `casaAlvo.pipelineAlvo.reportAlvo` | componente | Python, polars, deltalake |

`casaHoje.pipelineHoje.reportHoje`: Tasks classify e report do reclassificar e do finance_daily (classificam as transações e montam as tabelas do app no curated) e a semente carga_inicial_edicao, que preenche categorias e regras do edicao.db a partir do config/edicao quando estão vazias, só no finance_demo (não na prod); com as funções do relatório (cartao_listas, report_lib, saude)

`casaAlvo.pipelineAlvo.reportAlvo`: Tasks classify e report do reclassificar e do finance_daily (classificam as transações e montam as tabelas do app no curated) e a semente carga_inicial_edicao, que preenche categorias e regras do edicao.db a partir do config/edicao quando estão vazias, só no finance_demo (não na prod); com as funções do relatório (cartao_listas, report_lib, saude). Muda: a semente das edições sai do pipeline, que só lê o banco do app (df-8x0m.23.14)

Code: `workspaces/GetFinance/scripts/report`.
