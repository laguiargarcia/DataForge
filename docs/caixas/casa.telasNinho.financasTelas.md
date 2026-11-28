---
topic: "Tela de finanças"
read_when: "Working on the C4 box casaHoje.telasNinhoHoje.financasTelasHoje, casaAlvo.appCelularAlvo.financasTelasAlvo or the code it covers"
code:
  - ninho/telas/src/financas
status: esboço
---

# Tela de finanças

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.telasNinhoHoje.financasTelasHoje` | componente | React, TypeScript |
| `casaAlvo.appCelularAlvo.financasTelasAlvo` | componente | React, TypeScript |

`casaHoje.telasNinhoHoje.financasTelasHoje`: As transações de um mês, com a troca de mês; corrigir e desfazer a subcategoria de uma transação; o aviso de que há correções ainda não aplicadas; e aplicar as correções, seguindo a execução até o fim (o run_id fica no navegador, para retomar depois de recarregar)

`casaAlvo.appCelularAlvo.financasTelasAlvo`: As transações de um mês, com a troca de mês; corrigir e desfazer a subcategoria de uma transação; o aviso de que há correções ainda não aplicadas; e aplicar as correções, seguindo a execução até o fim (o run_id fica no navegador, para retomar depois de recarregar). Muda: passa a ter o que o app Finanças do PC tem, na ordem que o dono escolher (df-8x0m.23.19)

Code: `ninho/telas/src/financas`.
