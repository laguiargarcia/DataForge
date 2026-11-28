---
topic: "Regras de conferência e extrato"
read_when: "Working on the C4 box casaHoje.pipelineHoje.conferenciasHoje or the code it covers"
code:
  - workspaces/GetFinance/scripts/report/conferencia_fatura.py
  - workspaces/GetFinance/scripts/report/conferencia_saldo.py
  - workspaces/GetFinance/scripts/report/extrato.py
  - workspaces/GetFinance/scripts/report/lancamentos_suspeitos.py
  - workspaces/GetFinance/scripts/report/veredito_extrato.py
status: esboço
---

# Regras de conferência e extrato

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.conferenciasHoje` | componente | Python, polars |

`casaHoje.pipelineHoje.conferenciasHoje`: Funções puras de scripts/report que as tasks de conferência carregam: leitura do texto do extrato do banco, conferência de saldo e de fatura, lançamentos suspeitos e veredito do extrato

Code: `workspaces/GetFinance/scripts/report/conferencia_fatura.py`, `workspaces/GetFinance/scripts/report/conferencia_saldo.py`, `workspaces/GetFinance/scripts/report/extrato.py`, `workspaces/GetFinance/scripts/report/lancamentos_suspeitos.py`, `workspaces/GetFinance/scripts/report/veredito_extrato.py`.
