---
topic: "Núcleo do servidor"
read_when: "Working on the C4 box casaHoje.ninhoHoje.servidorHoje, casaAlvo.servidorAlvo.nucleoAlvo or the code it covers"
code:
  - ninho/__init__.py
  - ninho/servidor
status: esboço
---

# Núcleo do servidor

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.ninhoHoje.servidorHoje` | componente | Python, FastAPI, SQLite |
| `casaAlvo.servidorAlvo.nucleoAlvo` | componente | Python, FastAPI, SQLite |

`casaHoje.ninhoHoje.servidorHoje`: O app FastAPI (uvicorn ninho.servidor.app:app): entrar e sair por sessão com cookie, a pessoa da sessão e a checagem do módulo em cada rota, as rotas /api/financas, /api/eu e /api/pessoas, os routers da lista e dos desejos e o build das telas (ninho/telas/dist) em /; lê o NINHO_WORKSPACE, conferido pelo workspace.yaml da pasta, e passa a pasta às rotas de finanças; as tabelas pessoas (senha em scrypt) e sessoes do ninho.db, aberto pelo NINHO_BANCO

`casaAlvo.servidorAlvo.nucleoAlvo`: O app FastAPI (uvicorn ninho.servidor.app:app): entrar e sair por sessão com cookie, a pessoa da sessão e a checagem do módulo em cada rota, as rotas /api/financas, /api/eu e /api/pessoas, os routers da lista e dos desejos e o build das telas (ninho/telas/dist) em /; lê o NINHO_WORKSPACE, conferido pelo workspace.yaml da pasta, e passa a pasta às rotas de finanças; as tabelas pessoas (senha em scrypt) e sessoes do ninho.db, aberto pelo NINHO_BANCO. Muda: as rotas /api/financas saem deste arquivo para um router do módulo finanças, como os outros módulos (server-modules.md; df-8x0m.23.19), e o router da agenda entra (df-8x0m.23.21); o NINHO_WORKSPACE, hoje a pasta do workspace GetFinance (hoje confere pelo workspace.yaml que a pasta do NINHO_WORKSPACE é um workspace), muda quando o workspace vai para ninho/financas (df-8x0m.23.14)

Code: `ninho/__init__.py`, `ninho/servidor`.
