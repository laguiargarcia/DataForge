---
topic: "Cliente do backend e edição"
read_when: "Working on the C4 box casaHoje.frontFinancasHoje.clienteBackendFinancasHoje or the code it covers"
code:
  - workspaces/GetFinance/apps/financas/src/edicao.ts
status: esboço
---

# Cliente do backend e edição

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.frontFinancasHoje.clienteBackendFinancasHoje` | componente | TypeScript, fetch |

`casaHoje.frontFinancasHoje.clienteBackendFinancasHoje`: pedir: o cliente HTTP do backend do app, relativo à página (api/...), e as chamadas de edição (categorias, regras, correções, explicações), do Aplicar e do andamento de uma execução; os textos do efeito de uma regra e da faixa de pendência

Code: `workspaces/GetFinance/apps/financas/src/edicao.ts`.
