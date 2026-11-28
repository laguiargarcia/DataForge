---
topic: "Telas do ninho / App no celular"
read_when: "Working on the C4 box casaHoje.telasNinhoHoje, casaAlvo.appCelularAlvo or the code it covers"
code:
  - ninho/identidade
  - ninho/telas
status: esboço
---

# Telas do ninho / App no celular

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.telasNinhoHoje` | conteiner | React, TypeScript; web app no navegador do celular |
| `casaAlvo.appCelularAlvo` | conteiner | Web app instalada no celular |

`casaHoje.telasNinhoHoje`: As telas do ninho: entrar, lista de compras, desejos e correções de finanças, com a identidade visual do ninho

`casaAlvo.appCelularAlvo`: As telas do ninho (ninho/telas), abertas como app na tela de início. Muda: cópia local do que mostra e fila de mudanças enviada ao servidor quando a rede volta

Code: `ninho/identidade`, `ninho/telas`.
