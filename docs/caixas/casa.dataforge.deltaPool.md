---
topic: "Leituras Delta"
read_when: "Working on the C4 box casaHoje.dataforgeHoje.deltaPoolHoje, casaAlvo.dataforgeAlvo.deltaPoolAlvo or the code it covers"
code:
  - dataforge/delta_pool.py
status: esboço
---

# Leituras Delta

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.dataforgeHoje.deltaPoolHoje` | componente | Python, polars, deltalake |
| `casaAlvo.dataforgeAlvo.deltaPoolAlvo` | componente | Python, polars, deltalake |

`casaHoje.dataforgeHoje.deltaPoolHoje`: As leituras Delta de um processo longo em threads que vivem com ele (falha nativa no Windows, df-5lk8)

`casaAlvo.dataforgeAlvo.deltaPoolAlvo`: As leituras Delta de um processo longo em threads que vivem com ele (falha nativa no Windows, df-5lk8). Sai com os dois que a usam, a rota genérica de tabelas e o backend do app; o servidor do ninho lê o lago com o seu próprio pool (ninho/financas/leitura.py)

Code: `dataforge/delta_pool.py`.
