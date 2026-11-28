---
topic: "Cópia local e fila"
read_when: "Working on the C4 box casaAlvo.appCelularAlvo.copiaLocalAlvo or the code it covers"
code: []
status: esboço
---

# Cópia local e fila

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaAlvo.appCelularAlvo.copiaLocalAlvo` | componente | TypeScript |

`casaAlvo.appCelularAlvo.copiaLocalAlvo`: Guarda no aparelho o que as telas mostram e a fila das mudanças feitas sem rede, e envia a fila ao servidor quando a rede volta (a última escrita vence, por item). A lista e a agenda com certeza; o que mais entra (as edições de finanças, a sessão sem rede) decide df-8x0m.23.8; o aplicar exige rede
