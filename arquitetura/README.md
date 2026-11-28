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
  malformado ou com duas das três marcas, e num `bead` sem marca.
- `bd close <id>` (hook `scripts/beads/check.py`): recusa fechar um bead que o
  `arquitetura/modelo.json` do origin/main ainda marca. Para conferir tudo de uma vez, com bd:
  `python3 scripts/arquitetura/marcas.py --bd`.

## Regras do nível de componentes (C4 nível 3, df-8x0m.23.47)

Convenções:

- Componente é o tipo `componente` do `spec.c4`. Ids com o sufixo do modelo: `<nome>Hoje` no hoje,
  `<nome>Alvo` no alvo. Um arquivo por contêiner, `componentes-<conteiner>.c4`, com
  `extend casaHoje.<conteiner> { ... }`, e uma vista por contêiner,
  `view hoje-componentes-<conteiner> of casaHoje.<conteiner>`.
- Todo componente, e todo contêiner com código, tem `metadata { codigo [...] }`: caminhos a partir da
  raiz do repositório, arquivo ou pasta. A seta entre componentes leva `metadata { codigo [...] }`
  com os arquivos onde a chamada está, sem linha; o arquivo:linha vai nas notas do bead.
- Arquivo de código: `.py .ts .tsx .js .jsx .mjs .cjs .sh .ps1`. Fora (sem dono, imports não lidos):
  pastas `dist`, `node_modules`, `tests`, `test`, `__tests__`, `e2e`; `test_*.py`, `*_test.py`,
  `conftest.py`, `*.test.*`, `*.spec.*`; `*.config.{ts,js,mjs,cjs}` (vite, playwright).
- Import conta também o de tipo: `import type` no TS e o import sob `if TYPE_CHECKING:` no Python
  pedem seta como os outros.

`python3 scripts/arquitetura/componentes.py` confere, com a numeração da docstring dele:

1. Todo caminho de `codigo`, de elemento, seta ou item de implantação, do hoje e do alvo, está em
   forma normal (sem `./`, `..` nem `/` nas pontas) e é arquivo versionado ou pasta com arquivo
   versionado (`git ls-files`, não o disco).
2. O dono de um arquivo é o elemento de caminho mais longo que o contém. Contêineres se aninham por
   caminho, não no modelo: o arquivo de `apps/financas` é do contêiner dele, não do pipeline.
3. Cada import estático entre arquivos de donos diferentes tem seta, quando ao menos um dos donos é
   componente: origem = dono de quem importa, destino = dono do importado, exatamente (seta entre os
   pais ou entre os filhos não conta; título e kind livres). Não se cobra seta entre ancestral e
   descendente pelo id (o LikeC4 recusa seta pai-filho) nem entre dois contêineres sem componente.
   Quem acrescenta componentes a um contêiner depois re-aponta as setas que entram nele quando o
   checker pede (import) ou o contestador acha (HTTP, CLI).
4. Num contêiner com pelo menos um componente, nenhum arquivo de código tem o contêiner como dono
   direto: um componente dele (ou um contêiner aninhado por caminho) é o dono.
5. O mesmo caminho em dois elementos do mesmo sistema é erro (dono ambíguo).

Hoje e alvo são dois desenhos no mesmo `modelo.json`: 2 a 5 valem por sistema (primeira parte do
id), e 3 e 4 só no hoje (sistema terminado em `Hoje`), porque o alvo desenha código que ainda não
existe. O script sai 1 com uma linha `error:` por problema. Saem como `warning:`, sem falhar, a
lista que o contestador confere à mão: importação dinâmica, subprocesso que pode rodar Python, o
DataForge ou um shell, linha de `.sh`/`.ps1` que chama `dataforge` ou `python`, import de arquivo de
código sem dono. A docstring diz como resolve imports Python e TS. O job `arquitetura` do CI roda o
script depois do `conferir`, com o Python do `.python-version`.

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
