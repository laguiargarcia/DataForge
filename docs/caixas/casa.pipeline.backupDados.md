---
topic: "backup"
read_when: "Working on the C4 box casaHoje.pipelineHoje.backupDadosHoje, casaAlvo.pipelineAlvo.backupDadosAlvo or the code it covers"
code:
  - workspaces/GetFinance/scripts/backup
status: esboço
---

# backup

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.pipelineHoje.backupDadosHoje` | componente | Python |
| `casaAlvo.pipelineAlvo.backupDadosAlvo` | componente | Python |

`casaHoje.pipelineHoje.backupDadosHoje`: Task backup do job backup: zip datado de raw, edicao.db, ninho.db e extratos, guarda os mais novos

`casaAlvo.pipelineAlvo.backupDadosAlvo`: Task backup do job backup: zip datado de raw, edicao.db, ninho.db e extratos, guarda os mais novos. Muda: a cópia vai para fora deste disco, a decidir em df-8x0m.23.9

Code: `workspaces/GetFinance/scripts/backup`.
