---
topic: "Arquivos do workspace: workspace.yaml, jobs, conexões e config"
read_when: "Working on the C4 box casaHoje.pipelineHoje.arquivosWorkspaceHoje, casaAlvo.pipelineAlvo.arquivosWorkspaceAlvo or the code it covers"
code:
  - workspaces/GetFinance/config
  - workspaces/GetFinance/connections
  - workspaces/GetFinance/jobs
  - workspaces/GetFinance/workspace.yaml
status: esboço
---

# Arquivos do workspace: workspace.yaml, jobs, conexões e config

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.arquivosWorkspaceHoje` | banco | YAML e CSV |
| `casaAlvo.pipelineAlvo.arquivosWorkspaceAlvo` | banco | YAML e CSV |

`casaHoje.pipelineHoje.arquivosWorkspaceHoje`: Os arquivos que dizem o que o workspace roda e onde estão os dados: workspace.yaml (nome e engine), jobs/<job>/job.yaml (as tasks, com script, argumentos e depends_on), connections/connections.yaml (onde ficam raw, cleansed e curated do lago, o edicao.db, os achados das validações e os extratos) e config/ (instituições, estabelecimentos, investimentos sintéticos e os CSV da semente do edicao.db). A Definição do workspace, no DataForge, lê e escreve jobs e triggers; o Deploy copia jobs, config e triggers de um workspace a outro (sem uso em prod); o DataForge, as camadas, o ninho e o backend Finanças os leem. Os triggers/ e o config/investments.yaml existem só na prod, fora do git

`casaAlvo.pipelineAlvo.arquivosWorkspaceAlvo`: Os arquivos que dizem o que o workspace roda e onde estão os dados: workspace.yaml (nome e engine), jobs/<job>/job.yaml (as tasks, com script, argumentos e depends_on), connections/connections.yaml (onde ficam raw, cleansed e curated do lago, o edicao.db, os achados das validações e os extratos) e config/ (instituições, estabelecimentos, investimentos sintéticos e os CSV da semente do edicao.db). Muda: vai para ninho/financas com o módulo; o DataForge acha a pasta pelo registro, df-7cyk

Code: `workspaces/GetFinance/config`, `workspaces/GetFinance/connections`, `workspaces/GetFinance/jobs`, `workspaces/GetFinance/workspace.yaml`.
