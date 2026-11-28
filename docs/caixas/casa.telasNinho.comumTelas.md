---
topic: "Comum às telas"
read_when: "Working on the C4 box casaHoje.telasNinhoHoje.comumTelasHoje, casaAlvo.appCelularAlvo.comumTelasAlvo or the code it covers"
code:
  - ninho/telas/src/Logo.tsx
  - ninho/telas/src/dados.ts
  - ninho/telas/src/pedido.ts
status: esboço
---

# Comum às telas

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.telasNinhoHoje.comumTelasHoje` | componente | React, TypeScript |
| `casaAlvo.appCelularAlvo.comumTelasAlvo` | componente | React, TypeScript |

`casaHoje.telasNinhoHoje.comumTelasHoje`: O pedido ao servidor (o fetch com o cookie da sessão, a mensagem de uma recusa e o aviso à casca num 401), a leitura e releitura dos dados de uma tela de módulo, que a lista e os desejos usam, e o Logo, que importa o logo-ninho.svg de ninho/identidade

`casaAlvo.appCelularAlvo.comumTelasAlvo`: O pedido ao servidor (o fetch com o cookie da sessão, a mensagem de uma recusa e o aviso à casca num 401), a leitura e releitura dos dados de uma tela de módulo, que a lista e os desejos usam, e o Logo, que importa o logo-ninho.svg de ninho/identidade

Code: `ninho/telas/src/Logo.tsx`, `ninho/telas/src/dados.ts`, `ninho/telas/src/pedido.ts`.
