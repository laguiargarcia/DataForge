---
topic: "Hospedagem de apps"
read_when: "Working on the C4 box casaHoje.dataforgeHoje.appsHoje, casaAlvo.dataforgeAlvo.appsAlvo or the code it covers"
code:
  - dataforge/api/routers/apps.py
  - dataforge/app_backend_host.py
  - dataforge/app_backends.py
  - dataforge/app_guard.py
  - dataforge/apps.py
  - dataforge/shortcut.py
status: esboço
---

# Hospedagem de apps

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.dataforgeHoje.appsHoje` | componente | Python, uvicorn |
| `casaAlvo.dataforgeAlvo.appsAlvo` | componente | Python, uvicorn |

`casaHoje.dataforgeHoje.appsHoje`: Acha os apps dos workspaces (app.yaml, dist), inicia e para o backend de cada um num processo uvicorn com um token, o middleware que o backend usa para recusar o que não veio do DataForge e o atalho do Windows

`casaAlvo.dataforgeAlvo.appsAlvo`: Acha os apps dos workspaces (app.yaml, dist), inicia e para o backend de cada um num processo uvicorn com um token, o middleware que o backend usa para recusar o que não veio do DataForge, o atalho do Windows e as rotas que servem o build e repassam ao backend (api/routers/apps.py). Sai: o DataForge não hospeda app (ADR 0006)

Code: `dataforge/api/routers/apps.py`, `dataforge/app_backend_host.py`, `dataforge/app_backends.py`, `dataforge/app_guard.py`, `dataforge/apps.py`, `dataforge/shortcut.py`.
