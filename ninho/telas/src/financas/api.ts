// Cliente das rotas de finanças do servidor do ninho (ninho/servidor/app.py). A página vem do mesmo
// servidor, atrás do mesmo login (HTTP Basic, o nome e a senha da pessoa): o navegador pede ao abrir a
// página e manda de novo em cada pedido daqui, então nenhum pedido trata de senha.

/** Uma linha de fact_transaction_classified, com as colunas de ninho/financas/leitura.py (COLUNAS).
 * O dinheiro vem em reais, número JSON (Float64 da tabela): exato no centavo bem além de qualquer
 * valor desta casa; a tela só arredonda para 2 casas ao mostrar (brl em formato.ts). */
export type Transacao = {
  id: string
  date: string
  anomes_key: number
  description_normalized: string
  amount_signed: number
  account_type: string
  categoria: string | null
  subcategoria: string | null
  corrigida: boolean
}
export type Categoria = { subcategoria: string; categoria: string; tipo: string }
export type Correcao = { transacao_id: string; subcategoria: string }
export type Edicoes = {
  versao: number
  versao_aplicada: number | null
  pendente: boolean
  correcoes: Correcao[]
  categorias: Categoria[]
}
/** O estado de uma execução, como o motor responde (`dataforge run-state`). */
export type Execucao = {
  run_id: string
  state: string
  finished_at?: string | null
  failed_task?: string | null
  reason?: string | null
}

/** Pedido recusado, com a mensagem que a tela mostra e o status HTTP. */
export class ErroPedido extends Error {
  readonly status: number

  constructor(mensagem: string, status: number) {
    super(mensagem)
    this.status = status
  }
}

// Relativo à página, que o servidor entrega em "/": os pedidos vão ao mesmo servidor.
const endereco = (caminho: string) => `api/financas/${caminho}`

// Um id vira um segmento só: "/" e também "." viram %XX, para "." e ".." não serem lidos como
// segmentos de ponto e levarem o pedido a outra rota.
export const segmento = (valor: string) => encodeURIComponent(valor).replaceAll('.', '%2E')

async function pedir<T>(metodo: string, caminho: string, corpo?: unknown): Promise<T> {
  const opcoes: RequestInit = corpo === undefined
    ? { method: metodo }
    : { method: metodo, headers: { 'content-type': 'application/json' }, body: JSON.stringify(corpo) }
  const r = await fetch(endereco(caminho), opcoes)
  if (!r.ok) {
    const detalhe = await r.json().then((c: { detail?: unknown }) => c.detail, () => undefined)
    throw new ErroPedido(typeof detalhe === 'string' ? detalhe : `Falha no pedido: HTTP ${r.status}`, r.status)
  }
  return (r.status === 204 ? undefined : await r.json()) as T
}

export const lerTransacoes = (mes: string) =>
  pedir<{ mes: string; transacoes: Transacao[] }>('GET', `transacoes?mes=${mes}`).then(c => c.transacoes)
export const lerEdicoes = () => pedir<Edicoes>('GET', 'edicoes')
export const corrigir = (id: string, subcategoria: string) =>
  pedir<Correcao>('PUT', `correcoes/${segmento(id)}`, { subcategoria })
export const desfazer = (id: string) => pedir<void>('DELETE', `correcoes/${segmento(id)}`)
export const pedirAplicar = () => pedir<Execucao>('POST', 'aplicar')
/** O estado da execução; com `esperar` (segundos), o servidor só responde quando ela acaba ou o tempo passa. */
export const andamento = (runId: string, esperar: number) =>
  pedir<Execucao>('GET', `aplicar/${segmento(runId)}?esperar=${esperar}`)
