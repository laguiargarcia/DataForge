# Arquitetura (C4 em LikeC4)

Desenhos C4 do sistema, em texto, versionados aqui. Fonte do conteúdo: ADR 0006 e
`docs/architecture-v2/`. Quatro vistas, em dois arquivos de modelo:

| Vista | Arquivo | O que mostra |
| --- | --- | --- |
| `hoje-contexto`, `hoje-conteineres` | `hoje.c4` | O que roda em prod hoje. Muda quando a prod muda. |
| `alvo-contexto`, `alvo-conteineres` | `alvo.c4` | O alvo v2 (quatro peças). Tags `aFazer` e `aDecidir` marcam o que falta. |

`spec.c4` define os tipos de elemento e as tags. Nada é instalado no repositório: o LikeC4 roda por
`npx` com a versão fixa.

## Comandos (a partir da raiz do repositório)

```bash
npx likec4@1.61.1 start arquitetura        # prévia no navegador
npx likec4@1.61.1 validate arquitetura     # checa sintaxe e semântica
npx likec4@1.61.1 build arquitetura --output-single-file -o /tmp/likec4-out   # HTML único
```

`arquitetura/dist/` é ignorado pelo git, se preferir gerar a saída ali.

Repositório público: sem nomes reais de pessoas ("Dono da casa", "Outra pessoa da casa"), sem dados.
