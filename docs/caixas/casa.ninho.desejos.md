---
topic: "Lista de desejos"
read_when: "Working on the C4 box casaHoje.ninhoHoje.desejosHoje, casaAlvo.servidorAlvo.desejosAlvo or the code it covers"
code:
  - ninho/desejos
status: esboço
---

# Lista de desejos

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.ninhoHoje.desejosHoje` | componente | Python, FastAPI, SQLite |
| `casaAlvo.servidorAlvo.desejosAlvo` | componente | Python, FastAPI, SQLite |

`casaHoje.ninhoHoje.desejosHoje`: Rotas /api/desejos e as regras dos desejos, das anotações (um endereço http ou https inteiro é link) e do presente surpresa, que o dono do desejo nunca vê, nas tabelas desejos_* do ninho.db, criadas no primeiro uso

`casaAlvo.servidorAlvo.desejosAlvo`: Rotas /api/desejos e as regras dos desejos, das anotações (um endereço http ou https inteiro é link) e do presente surpresa, que o dono do desejo nunca vê, nas tabelas desejos_* do ninho.db, criadas no primeiro uso

Code: `ninho/desejos`.
