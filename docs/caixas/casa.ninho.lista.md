---
topic: "Lista de compras"
read_when: "Working on the C4 box casaHoje.ninhoHoje.listaHoje, casaAlvo.servidorAlvo.listaAlvo or the code it covers"
code:
  - ninho/lista
status: esboço
---

# Lista de compras

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.ninhoHoje.listaHoje` | componente | Python, FastAPI, SQLite |
| `casaAlvo.servidorAlvo.listaAlvo` | componente | Python, FastAPI, SQLite |

`casaHoje.ninhoHoje.listaHoje`: Rotas /api/lista e as regras dos itens (texto, comprado, quem adicionou) na tabela lista_itens do ninho.db, criada no primeiro uso

`casaAlvo.servidorAlvo.listaAlvo`: Rotas /api/lista e as regras dos itens (texto, comprado, quem adicionou) na tabela lista_itens do ninho.db, criada no primeiro uso

Code: `ninho/lista`.
