// Cliente do backend do app (apps/financas/backend/main.py) e os textos das telas de edição (df-cns.8).
// Salvar grava direto no banco; o efeito nos números só aparece depois do Aplicar, que roda o job
// reclassificar pela fila do workspace.

export type Categoria = { subcategoria: string; categoria: string; tipo: string }
export type Regra = { padrao: string; subcategoria: string }
export type Correcao = { transacao_id: string; subcategoria: string; encontrada: boolean }
export type Edicao = {
  categorias: Categoria[]
  regras: Regra[]
  correcoes: Correcao[]
  tipos: string[]
  versao: number
  aplicada: number | null
  pendente: boolean
}
export type Efeito = { casa: number; vence: number; barradas: number }
export type StatusExecucao = 'pending' | 'running' | 'success' | 'failed'
export type Andamento = { status: StatusExecucao; reason: string | null }

/** Recusa do backend, com a mensagem que a tela mostra na própria linha. */
export class ErroEdicao extends Error {}

// Os segmentos da URL voltam codificados (como em api.ts): o pedido fica sempre no mesmo app.
function endereco(caminho: string): string {
  const m = /^\/apps\/([^/]+)\/([^/]+)\//.exec(location.pathname)
  const [ws, app] = m
    ? [decodeURIComponent(m[1]), decodeURIComponent(m[2])]
    : [import.meta.env.VITE_WORKSPACE || 'GetFinance', 'financas']
  return `/apps/${encodeURIComponent(ws)}/${encodeURIComponent(app)}/api/${caminho}`
}

const seg = (valor: string) => encodeURIComponent(valor)

async function pedir<T>(metodo: string, caminho: string, corpo?: unknown): Promise<T> {
  const r = await fetch(endereco(caminho), {
    method: metodo,
    headers: corpo === undefined ? undefined : { 'content-type': 'application/json' },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  })
  if (!r.ok) {
    const detalhe = await r.json().then((c: { detail?: unknown }) => c.detail, () => undefined)
    throw new ErroEdicao(typeof detalhe === 'string' ? detalhe : `Falha ao gravar: HTTP ${r.status}`)
  }
  return (r.status === 204 ? undefined : await r.json()) as T
}

export const lerEdicao = () => pedir<Edicao>('GET', 'edicao')

export const salvarCategoria = (antiga: string | null, c: Categoria) =>
  pedir<Categoria>(antiga === null ? 'POST' : 'PUT', antiga === null ? 'categorias' : `categorias/${seg(antiga)}`, c)

export const excluirCategoria = (subcategoria: string) => pedir<void>('DELETE', `categorias/${seg(subcategoria)}`)

export async function salvarRegra(antigo: string | null, r: Regra): Promise<Efeito | null> {
  const resposta = await pedir<{ efeito: Efeito | null }>(
    antigo === null ? 'POST' : 'PUT', antigo === null ? 'regras' : `regras/${seg(antigo)}`, r)
  return resposta.efeito
}

export const excluirRegra = (padrao: string) => pedir<void>('DELETE', `regras/${seg(padrao)}`)

export const corrigir = (transacaoId: string, subcategoria: string) =>
  pedir<unknown>('PUT', `correcoes/${seg(transacaoId)}`, { subcategoria })

export const desfazerCorrecao = (transacaoId: string) => pedir<void>('DELETE', `correcoes/${seg(transacaoId)}`)

export async function aplicar(): Promise<string> {
  return (await pedir<{ execution_id: string }>('POST', 'aplicar')).execution_id
}

export const andamento = (executionId: string) => pedir<Andamento>('GET', `aplicar/${seg(executionId)}`)

// --- textos ---------------------------------------------------------------------------------------

const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`

/** O que a regra recém-salva faz nos dados de agora, e o aviso de direção (df-277v) se houver.
 *  Vale = onde ela é a regra mais longa e o portão de direção deixa passar. */
export function textoEfeito(efeito: Efeito, subcategoria: string): { resumo: string; aviso: string | null } {
  const casa = plural(efeito.casa, 'transação', 'transações')
  const resumo = efeito.casa === 0
    ? 'Nenhuma transação tem esse padrão por enquanto.'
    : `Casa ${casa}; vale em ${efeito.vence - efeito.barradas}.`
  if (efeito.barradas === 0) return { resumo, aviso: null }
  const k = plural(efeito.barradas, 'delas é', 'delas são')
  return {
    resumo,
    aviso: `${k} do lado oposto ao Tipo de «${subcategoria}» (entrada com Tipo de saída, ou o contrário): ` +
      'a regra não vale nelas, e elas continuam na categoria de antes.',
  }
}

/** Estado da faixa de pendência, de "em dia" até a falha do recálculo. */
export type Faixa =
  | { fase: 'em_dia' }
  | { fase: 'pendente' }
  | { fase: 'rodando'; status: 'pending' | 'running' }
  | { fase: 'falhou'; motivo: string | null }

export function textoFaixa(faixa: Faixa): string | null {
  switch (faixa.fase) {
    case 'em_dia': return null
    case 'pendente': return 'Há edições ainda não recalculadas.'
    case 'rodando': return faixa.status === 'pending' ? 'Na fila: esperando outro job do workspace terminar…' : 'Recalculando…'
    case 'falhou': {
      const motivo = faixa.motivo ? ': ' + faixa.motivo : ''
      return `O recálculo falhou${motivo}. As edições continuam salvas.`
    }
  }
}

/** Próxima fase da faixa a partir do andamento da execução pedida pelo Aplicar. */
export function faseDoAndamento(a: Andamento): Faixa | 'terminou' {
  if (a.status === 'success') return 'terminou'
  if (a.status === 'pending' || a.status === 'running') return { fase: 'rodando', status: a.status }
  return { fase: 'falhou', motivo: a.reason }
}
