# Arquitetura (C4 em LikeC4)

Desenhos C4 do sistema, em texto, versionados aqui. Fonte do conteúdo: a ADR 0006 e os contratos de
`docs/architecture-v2/` (locais, fora do git), o código citado e os beads. Cinco vistas, em dois
arquivos de modelo:

| Vista | Arquivo | O que mostra |
| --- | --- | --- |
| `hoje-contexto`, `hoje-conteineres` | `hoje.c4` | O que roda em prod hoje. Muda quando a prod muda. |
| `alvo-contexto`, `alvo-conteineres` | `alvo.c4` | O alvo v2 (quatro peças). As marcas `novo`, `muda` e `sai` dizem o que difere de hoje (abaixo). |
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

## Marcas do alvo (df-8x0m.23.37)

Todo elemento ou seta de `alvo.c4` que difere de `hoje.c4` leva uma marca só, e o bead aberto da
mudança em `metadata` (um id ou uma lista):

| Marca | Quer dizer | Cor nas vistas do alvo |
| --- | --- | --- |
| `#novo` | ainda não existe | verde, borda tracejada |
| `#muda` | existe e vai mudar | âmbar |
| `#sai` | existe hoje e vai sumir; fica no alvo, marcado | vermelho, borda pontilhada |

```
servidorAlvo = conteiner 'Servidor da casa (ninho)' {
  #muda
  metadata {
    bead ['df-8x0m.23.19', 'df-8x0m.23.21']
  }
}
```

Sem marca: já roda em prod como está. O que some de hoje e não fica desenhado no alvo: os bancos
`edicao.db` e `ninho.db`, que viram o banco do app, e os extratos em PDF e os zips do backup,
citados na descrição dele (com os beads dele); a seta do backend que grava o PDF some com o backend,
até df-8x0m.23.35 dizer onde os extratos moram. A tag vem primeiro no corpo do elemento ou da seta.
Quando o bead fecha, a marca sai, ou passa para o bead que continua a mudança (um bead de decisão a
passa para os beads de implementação que criou). A conferência tem duas metades, porque o CI não tem
bd:

- `python3 scripts/arquitetura/marcas.py` (no CI): falha num item marcado sem bead, com id
  malformado ou com duas das três marcas, e num `bead` sem marca. Falha também numa marca no modelo
  de hoje (df-8x0m.23.46): todo elemento de topo tem id terminado em `Hoje` (o que descreve hoje, em
  `hoje.c4` ou em qualquer arquivo que estende seus elementos) ou em `Alvo`; um elemento marcado sob
  um topo `...Hoje`, ou uma seta marcada com uma ponta lá, falha, e um elemento de topo com outro
  sufixo também. O export do LikeC4 não diz de que arquivo vem cada item, por isso o sufixo; só o
  segmento de topo conta (`casaAlvo.velhoHoje` é do alvo).
- `bd close <id>` (hook `scripts/beads/check.py`): recusa fechar um bead que o
  `arquitetura/modelo.json` do origin/main ainda marca. Para conferir tudo de uma vez, com bd:
  `python3 scripts/arquitetura/marcas.py --bd`.

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
