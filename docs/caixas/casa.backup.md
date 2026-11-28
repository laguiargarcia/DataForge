---
topic: "Zips do backup"
read_when: "Working on the C4 box casaHoje.backupHoje or the code it covers"
code: []
status: esboço
---

# Zips do backup

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.backupHoje` | banco | Pasta de zips, no mesmo disco |

`casaHoje.backupHoje`: Zip semanal de raw, edicao.db, ninho.db e extratos, fora de qualquer repositório; guarda os 8 mais novos. Ainda no mesmo disco (df-8x0m.19)
