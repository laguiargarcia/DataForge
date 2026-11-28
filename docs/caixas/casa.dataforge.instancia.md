---
topic: "Instância e presença"
read_when: "Working on the C4 box casaHoje.dataforgeHoje.instanciaHoje, casaAlvo.dataforgeAlvo.instanciaAlvo or the code it covers"
code:
  - dataforge/instance.py
  - dataforge/presence.py
status: esboço
---

# Instância e presença

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.dataforgeHoje.instanciaHoje` | componente | Python, httpx |
| `casaAlvo.dataforgeAlvo.instanciaAlvo` | componente | Python, httpx |

`casaHoje.dataforgeHoje.instanciaHoje`: Qual DataForge tem a porta da API (/health), o início do processo em segundo plano ou de um processo destacado, e o registro das janelas abertas

`casaAlvo.dataforgeAlvo.instanciaAlvo`: Qual DataForge tem a porta da API (/health), o início do processo em segundo plano ou de um processo destacado, e o registro das janelas abertas. Muda: o registro também conta as janelas de app, que saem; o que fica, a decidir em df-8x0m.23.3

Code: `dataforge/instance.py`, `dataforge/presence.py`.
