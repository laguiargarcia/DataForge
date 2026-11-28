---
topic: "Front do app Finanças"
read_when: "Working on the C4 box casaHoje.frontFinancasHoje, casaAlvo.frontFinancasAlvo or the code it covers"
code:
  - workspaces/GetFinance/apps/financas
status: esboço
---

# Front do app Finanças

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.frontFinancasHoje` | conteiner | React, TypeScript; na janela Qt do DataForge ou no navegador do PC |
| `casaAlvo.frontFinancasAlvo` | conteiner | React, TypeScript; na janela Qt do DataForge ou no navegador do PC |

`casaHoje.frontFinancasHoje`: As telas do app Finanças: tabelas, edição de categorias, regras e correções, conferência e extratos

`casaAlvo.frontFinancasAlvo`: As telas do app Finanças no PC: tabelas, edição de categorias, regras e correções, conferência e extratos. Sai: as telas passam para a tela de finanças do app no celular (df-8x0m.23.19), e o DataForge deixa de servir o build e a rota de tabelas (df-8x0m.23.3)

Code: `workspaces/GetFinance/apps/financas`.
