---
topic: "Tabelas publicadas"
read_when: "Working on the C4 box casaAlvo.pipelineAlvo.publicadasAlvo or the code it covers"
code: []
status: esboço
---

# Tabelas publicadas

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaAlvo.pipelineAlvo.publicadasAlvo` | componente | Python |

`casaAlvo.pipelineAlvo.publicadasAlvo`: A lista das tabelas que o pipeline publica para o servidor, com o esquema declarado no módulo finanças, e a task que, ao fim de cada job que escreve uma delas, compara as tabelas com o esquema e falha a rodada quando diferem
