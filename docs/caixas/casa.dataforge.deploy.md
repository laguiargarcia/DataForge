---
topic: "Deploy"
read_when: "Working on the C4 box casaHoje.dataforgeHoje.deployHoje, casaAlvo.dataforgeAlvo.deployAlvo or the code it covers"
code:
  - dataforge/deploy
status: esboço
---

# Deploy

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.dataforgeHoje.deployHoje` | componente | Python |
| `casaAlvo.dataforgeAlvo.deployAlvo` | componente | Python |

`casaHoje.dataforgeHoje.deployHoje`: Plano, aplicação e rollback do deploy de um workspace para outro, com o ledger; o subcomando deploy da CLI; sem uso em prod hoje: um workspace só

`casaAlvo.dataforgeAlvo.deployAlvo`: Plano, aplicação e rollback do deploy de um workspace para outro, com o ledger; o subcomando deploy da CLI; sem uso em prod hoje: um workspace só. Muda: candidato a sair ou congelar, a decidir em df-8x0m.23.3

Code: `dataforge/deploy`.
