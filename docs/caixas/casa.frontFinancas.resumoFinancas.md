---
topic: "Resumo do mês"
read_when: "Working on the C4 box casaHoje.frontFinancasHoje.resumoFinancasHoje or the code it covers"
code:
  - workspaces/GetFinance/apps/financas/src/resumo.ts
status: esboço
---

# Resumo do mês

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.frontFinancasHoje.resumoFinancasHoje` | componente | TypeScript |

`casaHoje.frontFinancasHoje.resumoFinancasHoje`: Funções puras: escolhe as linhas do mês nas tabelas do report e as transações de cada categoria, e formata valores e meses; as regras estão no pipeline

Code: `workspaces/GetFinance/apps/financas/src/resumo.ts`.
