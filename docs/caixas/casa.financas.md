---
topic: "Backend GetFinance"
read_when: "Working on the C4 box casaHoje.financasHoje, casaAlvo.financasAlvo or the code it covers"
code:
  - workspaces/GetFinance/apps/financas/backend
status: esboço
---

# Backend GetFinance

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.financasHoje` | conteiner | Python, FastAPI |
| `casaAlvo.financasAlvo` | conteiner | Python, FastAPI |

`casaHoje.financasHoje`: Processo do backend do app Finanças; importa internos do DataForge, que por sua vez o inicia e lhe repassa as chamadas: acoplamento nos dois sentidos, dor do ADR 0006

`casaAlvo.financasAlvo`: O backend do app Finanças do PC, iniciado pelo DataForge: segundo escritor das edições, lê o lago direto e importa internos do DataForge e as regras do pipeline. Sai: o que ele faz passa para o módulo finanças do servidor (df-8x0m.23.19), e o DataForge deixa de hospedar apps (df-8x0m.23.3)

Code: `workspaces/GetFinance/apps/financas/backend`.
