// Cliente das rotas da lista de desejos (ninho/desejos/rotas.py) e das pessoas da casa (/api/pessoas,
// ninho/servidor/app.py), e as contas da tela: as três partes (de cada pessoa e dos dois) e a parte
// guardada no aparelho.

import { pedir as pedirAoServidor, segmento } from '../pedido'

/** Uma anotação: `link` diz se o texto inteiro é um endereço http ou https (o servidor decide). */
export type Anotacao = { id: string; texto: string; link: boolean }

/** Um desejo, como ninho/desejos/desejos.py o devolve (horas em ISO, UTC). `de_quem` é o id de uma
 * pessoa, ou null para os dois. */
export type Desejo = {
  id: string
  titulo: string
  de_quem: string | null
  adicionado_por: string
  realizado: boolean
  realizado_em: string | null
  criado_em: string
  anotacoes: Anotacao[]
}

export type Pessoa = { id: string; nome: string }
export type Edicao = { titulo?: string; de_quem?: string | null; realizado?: boolean }

/** Os limites de ninho/desejos/desejos.py: título, anotação e anotações por desejo. */
export const LIMITE_TITULO = 200
export const LIMITE_ANOTACAO = 2000
export const LIMITE_ANOTACOES = 50

const pedir = <T>(metodo: string, caminho: string, corpo?: unknown) =>
  pedirAoServidor<T>(metodo, `api/desejos${caminho}`, corpo)

/** Todos os desejos: os não realizados primeiro (mais novos em cima), depois os realizados. */
export const lerDesejos = () => pedir<{ desejos: Desejo[] }>('GET', '').then(c => c.desejos)
export const lerPessoas = () => pedirAoServidor<{ pessoas: Pessoa[] }>('GET', 'api/pessoas').then(c => c.pessoas)
export const adicionar = (titulo: string, de_quem: string | null) => pedir<Desejo>('POST', '', { titulo, de_quem })
export const editar = (id: string, mudancas: Edicao) => pedir<Desejo>('PATCH', `/${segmento(id)}`, mudancas)
export const apagar = (id: string) => pedir<void>('DELETE', `/${segmento(id)}`)
export const anotar = (id: string, texto: string) => pedir<Desejo>('POST', `/${segmento(id)}/anotacoes`, { texto })
export const desanotar = (id: string, anotacao: string) =>
  pedir<void>('DELETE', `/${segmento(id)}/anotacoes/${segmento(anotacao)}`)

/** A chave da parte dos dois: o id de uma pessoa nunca é vazio. */
export const DOS_DOIS = ''

export type Parte = { chave: string; rotulo: string }

/** As partes da tela: uma por pessoa, na ordem do servidor, e a dos dois no fim. */
export const partes = (pessoas: readonly Pessoa[]): Parte[] =>
  [...pessoas.map(p => ({ chave: p.id, rotulo: p.nome })), { chave: DOS_DOIS, rotulo: 'Nós dois' }]

/** A chave da parte de um desejo. */
export const parteDe = (desejo: Desejo) => desejo.de_quem ?? DOS_DOIS

/** Os desejos de uma parte, na ordem em que vieram: os abertos e os realizados. */
export function daParte(desejos: readonly Desejo[], chave: string): { abertos: Desejo[]; realizados: Desejo[] } {
  const da = desejos.filter(d => parteDe(d) === chave)
  return { abertos: da.filter(d => !d.realizado), realizados: da.filter(d => d.realizado) }
}

/** A parte a abrir: a guardada no aparelho, se ainda existe; senão a de quem entrou; senão a dos dois. */
export function parteInicial(guardada: string | null, todas: readonly Parte[], eu: string): string {
  const existe = (chave: string | null) => chave !== null && todas.some(p => p.chave === chave)
  if (existe(guardada)) return guardada!
  return existe(eu) ? eu : DOS_DOIS
}

// A última parte usada fica no aparelho. Sem armazenamento (modo privado, dado bloqueado) a tela
// segue sem lembrar, nunca quebra.
const CHAVE = 'ninho.desejos.parte'

export function lerParteGuardada(): string | null {
  try {
    return localStorage.getItem(CHAVE)
  } catch {
    return null
  }
}

export function guardarParte(chave: string): void {
  try {
    localStorage.setItem(CHAVE, chave)
  } catch {
    // sem armazenamento: a próxima abertura volta à parte de quem entrou
  }
}
