---
topic: "Finanças"
read_when: "Working on the C4 box casaHoje.ninhoHoje.financasNinhoHoje, casaAlvo.servidorAlvo.financasNinhoAlvo or the code it covers"
code:
  - ninho/banco.py
  - ninho/financas
status: esboço
---

# Finanças

Sketch (`status: esboço`), written by `python3 scripts/arquitetura/docs.py esbocar` from the C4 model (`arquitetura/`). Write the real doc over it and set `status: pronto`.

| Element | Kind | Technology |
| --- | --- | --- |
| `casaHoje.ninhoHoje.financasNinhoHoje` | componente | Python, polars, deltalake, SQLite |
| `casaAlvo.servidorAlvo.financasNinhoAlvo` | componente | Python, polars, deltalake, SQLite |

`casaHoje.ninhoHoje.financasNinhoHoje`: O que as rotas /api/financas fazem: lê as transações do mês e a versão aplicada no curated; lê no connections.yaml do workspace onde está o edicao.db; nele, pelas regras do workspace, que carrega do scripts/report dele (provisório, df-8x0m.23.14), lê as categorias, as correções e a versão das edições e grava as correções (cada gravação sobe a versão); compara essa versão com a aplicada no curated para dizer se há edição pendente; e pede ao DataForge (NINHO_DATAFORGE, senão o dataforge do mesmo Python, com o DATAFORGE_HOME dois níveis acima do workspace) o job reclassificar que as aplica

`casaAlvo.servidorAlvo.financasNinhoAlvo`: O que as rotas /api/financas fazem: lê as transações do mês e a versão aplicada no curated; lê e grava as correções no banco de edições, pelas regras do workspace; compara a versão das edições com a aplicada para dizer se há edição pendente; e pede ao DataForge, pela CLI, o job reclassificar que as aplica. Muda: faz o que o app Finanças do PC faz (categorias, regras, explicações, conferência, extratos; df-8x0m.23.19), e passa a ser o único escritor das edições; lê só as tabelas publicadas, por rotas de domínio (df-8x0m.23.45); importa as regras como pacote, sem sys.path (df-8x0m.23.14), e cria as tabelas das edições e a semente de categorias e regras, que hoje o pipeline grava (edits.md); com o registro de pastas do DataForge (df-7cyk), pede o job pelo nome e deixa de pôr o DATAFORGE_HOME dois níveis acima do workspace; hoje acha o edicao.db pelo connections.yaml do workspace; muda com df-8x0m.23.14; a regra do que cada pessoa vê (df-8x0m.23.6)

Code: `ninho/banco.py`, `ninho/financas`.
