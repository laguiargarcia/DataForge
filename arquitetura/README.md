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
npx likec4@1.61.1 validate arquitetura     # checa sintaxe e semântica
npx likec4@1.61.1 build arquitetura --output-single-file -o /tmp/likec4-out   # HTML único
```

`arquitetura/dist/` é ignorado pelo git, se preferir gerar a saída ali.

O job `arquitetura` do CI (`.github/workflows/ci.yml`) roda o mesmo `validate`, e o check obrigatório
`tests` depende dele: um `.c4` quebrado barra o merge. Ao trocar a versão do LikeC4, troque nos dois
lugares.

Repositório público: sem nomes reais de pessoas ("Dono da casa", "Outra pessoa da casa"), sem dados.
