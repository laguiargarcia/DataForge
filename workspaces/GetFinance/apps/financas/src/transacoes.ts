// A lista de transações da aba Transações (df-cns.8): busca, filtros e o estado da correção manual de
// cada linha. Funções puras sobre o curated fact_transaction_classified e as correções do banco.
import { rotuloMes, type TxClassificada } from './resumo'
import type { Correcao } from './edicao'

export const SEM_CATEGORIA = '(sem categoria)'
export const LIMITE_LINHAS = 300

export type Filtro = { busca: string; mes: number | null; categoria: string | null }

const semAcento = (texto: string) => texto.normalize('NFD').replaceAll(/\p{Diacritic}/gu, '').toLowerCase()

/** Filtra e ordena (mais recente primeiro). A busca ignora maiúsculas e acentos e olha a descrição e
 *  o estabelecimento. */
export function filtrar(txs: TxClassificada[], filtro: Filtro): TxClassificada[] {
  const busca = semAcento(filtro.busca.trim())
  return txs
    .filter(t => filtro.mes === null || t.anomes_key === filtro.mes)
    .filter(t => filtro.categoria === null || t.categoria === filtro.categoria)
    .filter(t => !busca || semAcento(`${t.description_normalized ?? ''} ${t.etl_merchant_key ?? ''}`).includes(busca))
    .sort((a, b) => b.date.localeCompare(a.date) || a.id.localeCompare(b.id))
}

/** Os meses com transação, do mais recente ao mais antigo. */
export function mesesDe(txs: TxClassificada[]): number[] {
  return [...new Set(txs.map(t => t.anomes_key))].sort((a, b) => b - a)
}

export const rotuloAnomes = (anomes: number) =>
  rotuloMes(`${Math.floor(anomes / 100)}-${String(anomes % 100).padStart(2, '0')}`)

/** As categorias presentes, em ordem alfabética, com "(sem categoria)" primeiro. */
export function categoriasDe(txs: TxClassificada[]): string[] {
  const todas = [...new Set(txs.map(t => t.categoria))].filter(c => c !== SEM_CATEGORIA).sort((a, b) => a.localeCompare(b, 'pt-BR'))
  return txs.some(t => t.categoria === SEM_CATEGORIA) ? [SEM_CATEGORIA, ...todas] : todas
}

export const dataCurta = (data: string) => `${data.slice(8, 10)}/${data.slice(5, 7)}/${data.slice(0, 4)}`

/** Marcação automática do ETL que uma correção sobrepõe, em palavras, ou null. */
export function marcacaoAutomatica(etlTag: string | null): string | null {
  if (etlTag === 'investment') return 'investimento'
  if (etlTag === 'card_payment_bank' || etlTag === 'card_payment_card') return 'pagamento de cartão'
  return null
}

export function textoConfirmacao(t: TxClassificada, subcategoria: string): string | null {
  const marca = marcacaoAutomatica(t.etl_tag)
  if (marca === null) return null
  return `Esta transação foi marcada automaticamente como ${marca}. A correção passa por cima dessa ` +
    `marcação: depois do Aplicar ela deixa de contar como ${marca} e passa a valer como «${subcategoria}». Continuar?`
}

/** Estado da correção de uma linha: o banco já pode ter mudado e o curated ainda não (falta Aplicar). */
export type EstadoCorrecao =
  | { tipo: 'nenhuma' }
  | { tipo: 'aplicada'; subcategoria: string }
  | { tipo: 'a_aplicar'; subcategoria: string }
  | { tipo: 'desfeita_a_aplicar' }

export function estadoCorrecao(t: TxClassificada, correcoes: Map<string, string>): EstadoCorrecao {
  const sub = correcoes.get(t.id)
  if (sub === undefined) return t.corrigida ? { tipo: 'desfeita_a_aplicar' } : { tipo: 'nenhuma' }
  return t.corrigida && t.subcategoria === sub ? { tipo: 'aplicada', subcategoria: sub } : { tipo: 'a_aplicar', subcategoria: sub }
}

export function textoEstado(e: EstadoCorrecao): string | null {
  switch (e.tipo) {
    case 'nenhuma': return null
    case 'aplicada': return 'Corrigida à mão'
    case 'a_aplicar': return `Corrigida para «${e.subcategoria}» (falta Aplicar)`
    case 'desfeita_a_aplicar': return 'Correção desfeita (falta Aplicar)'
  }
}

export const correcoesPorId = (correcoes: Correcao[]) => new Map(correcoes.map(c => [c.transacao_id, c.subcategoria]))

export const orfas = (correcoes: Correcao[]) => correcoes.filter(c => !c.encontrada)
