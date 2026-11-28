---
topic: "Regras de edição e classificação"
read_when: "Working on the C4 box casaHoje.pipelineHoje.regrasHoje, casaAlvo.pipelineAlvo.regrasAlvo or the code it covers"
code:
  - workspaces/GetFinance/scripts/report/classificacao.py
  - workspaces/GetFinance/scripts/report/edicao.py
status: esboço
---

# Regras de edição e classificação

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.regrasHoje` | componente | Python, SQLite |
| `casaAlvo.pipelineAlvo.regrasAlvo` | componente | Python, SQLite |

`casaHoje.pipelineHoje.regrasHoje`: edicao (abre o edicao.db e lê e grava categorias, regras, correções e explicações) e classificacao (classifica pela regra); módulos soltos que o backend Finanças e o ninho também carregam por sys.path

`casaAlvo.pipelineAlvo.regrasAlvo`: edicao (abre o edicao.db e lê e grava categorias, regras, correções e explicações) e classificacao (classifica pela regra); módulos soltos que o backend Finanças e o ninho também carregam por sys.path. Muda: viram um pacote importável do módulo finanças, que o pipeline e o servidor importam, sem sys.path (df-8x0m.23.14)

Code: `workspaces/GetFinance/scripts/report/classificacao.py`, `workspaces/GetFinance/scripts/report/edicao.py`.
