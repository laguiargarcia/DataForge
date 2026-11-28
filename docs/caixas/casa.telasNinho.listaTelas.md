---
topic: "Tela da lista de compras"
read_when: "Working on the C4 box casaHoje.telasNinhoHoje.listaTelasHoje, casaAlvo.appCelularAlvo.listaTelasAlvo or the code it covers"
code:
  - ninho/telas/src/lista
status: esboço
---

# Tela da lista de compras

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.telasNinhoHoje.listaTelasHoje` | componente | React, TypeScript |
| `casaAlvo.appCelularAlvo.listaTelasAlvo` | componente | React, TypeScript |

`casaHoje.telasNinhoHoje.listaTelasHoje`: Os itens da lista: acrescentar um item (o campo limpa e fica pronto para o próximo), marcar comprado, apagar e limpar os comprados; relê ao abrir e quando a página volta a aparecer

`casaAlvo.appCelularAlvo.listaTelasAlvo`: Os itens da lista: acrescentar um item (o campo limpa e fica pronto para o próximo), marcar comprado, apagar e limpar os comprados; relê ao abrir e quando a página volta a aparecer. Muda: sem rede, mostra a cópia local e põe as mudanças na fila (df-8x0m.23.8)

Code: `ninho/telas/src/lista`.
