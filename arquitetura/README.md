# Arquitetura (C4 em LikeC4)

Desenhos C4 do sistema, em texto, versionados aqui. Fonte do conteúdo: a ADR 0006 e os contratos de
`docs/architecture-v2/` (locais, fora do git), o código citado e os beads. Cinco vistas, em dois
arquivos de modelo:

| Vista | Arquivo | O que mostra |
| --- | --- | --- |
| `hoje-contexto`, `hoje-conteineres` | `hoje.c4` | O que roda em prod hoje. Muda quando a prod muda. |
| `alvo-contexto`, `alvo-conteineres` | `alvo.c4` | O alvo v2 (quatro peças). A tag `aFazer` marca o que falta, `emProd` o que já roda. |
| `index` | `alvo.c4` | Só os dois sistemas, hoje e alvo, como entrada. |

## Regras do desenho

1. A seta vai de quem inicia a ação para quem é chamado (C4), não no sentido do dado (decisão do
   dono, 2026-11-27, df-8x0m.23.33).
2. `-[leitura]->` marca uma relação só de leitura; ela aparece pontilhada.
3. Duas relações no mesmo par viram `[...]` no desenho, então se juntam num rótulo só. Isso vale
   também para pessoa -> sistema nas vistas de contexto: lá o rótulo único é dado na própria vista,
   com `include pessoa -> sistema with { title '...' }`.
4. Cada caixa, seta e rótulo tem fonte conferível (código com arquivo:linha, doc ou bead); o que
   ainda falta cita o bead.

`spec.c4` define os tipos de elemento e as tags. Nada é instalado no repositório: o LikeC4 roda por
`npx` com a versão fixa.

## Comandos (a partir da raiz do repositório)

```bash
npx likec4@1.61.1 start arquitetura        # prévia no navegador
npx likec4@1.61.1 validate --max-warnings 0 arquitetura   # sintaxe e semântica; aviso também falha
npx likec4@1.61.1 build arquitetura --output-single-file -o /tmp/likec4-out   # HTML único
```

`arquitetura/dist/` é ignorado pelo git, se preferir gerar a saída ali.

O job `arquitetura` do CI (`.github/workflows/ci.yml`) roda o mesmo `validate`, e o check obrigatório
`tests` depende dele: um `.c4` quebrado barra o merge.

`modelo.json` é o modelo exportado, enxuto e ordenado, que os scripts em Python leem
(`scripts/arquitetura/modelo.py`, cuja docstring dá a API e o formato). Mudou um `.c4`, rode
`python3 scripts/arquitetura/modelo.py gerar` e versione o `modelo.json` junto: o mesmo job do CI
falha quando ele não é o export dos `.c4`. Duas setas com a mesma origem, o mesmo destino e o mesmo
título (duas sem título também, mesmo com kind ou tags diferentes) param o `gerar` e o CI: dê a elas
títulos distintos.

Um PR que muda o `modelo.json` ganha um comentário do CI (job `report`) com o que mudou no desenho:
elementos, setas e vistas novos, removidos ou alterados (`scripts/arquitetura/diff.py`). O comentário
é editado a cada push e apagado quando o PR deixa de mudar o desenho. Para ver o mesmo texto antes do
PR: `git show origin/main:arquitetura/modelo.json | python3 scripts/arquitetura/diff.py`.

Ao trocar a versão do LikeC4, troque aqui, no `LIKEC4_VERSION` do job do CI e em `LIKEC4` de
`modelo.py` (um teste confere os três).

Repositório público: sem nomes reais de pessoas ("Dono da casa", "Outra pessoa da casa"), sem dados.
