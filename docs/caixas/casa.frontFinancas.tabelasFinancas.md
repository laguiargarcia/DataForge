---
topic: "Leitura de tabelas"
read_when: "Working on the C4 box casaHoje.frontFinancasHoje.tabelasFinancasHoje or the code it covers"
code:
  - workspaces/GetFinance/apps/financas/src/api.ts
status: esboço
---

# Leitura de tabelas

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.frontFinancasHoje.tabelasFinancasHoje` | componente | TypeScript, fetch |

`casaHoje.frontFinancasHoje.tabelasFinancasHoje`: lerTabela: lê uma tabela do curated pela rota genérica de tabelas do DataForge, com o workspace tirado da URL do app

Code: `workspaces/GetFinance/apps/financas/src/api.ts`.
