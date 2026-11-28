---
topic: "Casca do app"
read_when: "Working on the C4 box casaHoje.frontFinancasHoje.cascaFinancasHoje or the code it covers"
code:
  - workspaces/GetFinance/apps/financas/index.html
  - workspaces/GetFinance/apps/financas/src/App.tsx
  - workspaces/GetFinance/apps/financas/src/main.tsx
status: esboço
---

# Casca do app

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.frontFinancasHoje.cascaFinancasHoje` | componente | React, TypeScript |

`casaHoje.frontFinancasHoje.cascaFinancasHoje`: A página (index.html, de que o vite faz a do build), o ponto de entrada (main.tsx) e o App: as abas, a tela Resumo com o mês escolhido, a carga das tabelas do report, das transações classificadas e do banco de edição, e a recarga depois do Aplicar

Code: `workspaces/GetFinance/apps/financas/index.html`, `workspaces/GetFinance/apps/financas/src/App.tsx`, `workspaces/GetFinance/apps/financas/src/main.tsx`.
