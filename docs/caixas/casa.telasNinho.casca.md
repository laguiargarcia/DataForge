---
topic: "Casca"
read_when: "Working on the C4 box casaHoje.telasNinhoHoje.cascaHoje, casaAlvo.appCelularAlvo.cascaAlvo or the code it covers"
code:
  - ninho/telas/index.html
  - ninho/telas/public
  - ninho/telas/src/App.tsx
  - ninho/telas/src/Entrar.tsx
  - ninho/telas/src/eu.ts
  - ninho/telas/src/index.css
  - ninho/telas/src/main.tsx
status: esboço
---

# Casca

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.telasNinhoHoje.cascaHoje` | componente | React, TypeScript |
| `casaAlvo.appCelularAlvo.cascaAlvo` | componente | React, TypeScript |

`casaHoje.telasNinhoHoje.cascaHoje`: A página (index.html, main.tsx, a folha de estilo com as cores da identidade) e o App: pergunta quem entrou antes de montar qualquer tela, mostra o formulário de entrar sem sessão, monta só as telas dos módulos da pessoa e troca entre elas pela barra de baixo, com o Sair

`casaAlvo.appCelularAlvo.cascaAlvo`: A página (index.html, main.tsx, a folha de estilo com as cores da identidade) e o App: pergunta quem entrou antes de montar qualquer tela, mostra o formulário de entrar sem sessão, monta só as telas dos módulos da pessoa e troca entre elas pela barra de baixo, com o Sair. Muda: monta também a tela da agenda (df-8x0m.23.21); sem rede, saber quem entrou e abrir as telas, a decidir em df-8x0m.23.8

Code: `ninho/telas/index.html`, `ninho/telas/public`, `ninho/telas/src/App.tsx`, `ninho/telas/src/Entrar.tsx`, `ninho/telas/src/eu.ts`, `ninho/telas/src/index.css`, `ninho/telas/src/main.tsx`.
