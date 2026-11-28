---
topic: "Rotas do backend Finanças"
read_when: "Working on the C4 box casaHoje.financasHoje.rotasFinancasHoje or the code it covers"
code:
  - workspaces/GetFinance/apps/financas/backend/__init__.py
  - workspaces/GetFinance/apps/financas/backend/main.py
status: esboço
---

# Rotas do backend Finanças

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.financasHoje.rotasFinancasHoje` | componente | Python, FastAPI, polars, pypdf, httpx, PyYAML |

`casaHoje.financasHoje.rotasFinancasHoje`: O app FastAPI (main.py) atrás do middleware do token: lê e grava categorias, regras, correções e explicações do edicao.db com as validações de edicao, mostra o efeito de uma regra nos dados de agora, recebe o extrato do banco em PDF (confere, guarda em data/extratos, lista os guardados) e pede os jobs reclassificar e extrato à API do DataForge; acha o lago, o edicao.db e os extratos pelo connections.yaml do workspace

Code: `workspaces/GetFinance/apps/financas/backend/__init__.py`, `workspaces/GetFinance/apps/financas/backend/main.py`.
