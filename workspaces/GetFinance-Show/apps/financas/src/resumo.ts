// PROVISÓRIO: passa para o ETL com o df-cns
// Regras do Resumo em módulo puro. Datas são texto YYYY-MM-DD: nunca vão para Date.

export type Tx = {
  id: string
  date: string
  account_type: string
  etl_merchant_key: string | null
  amount_signed: number
  category: string | null
}
export type Fatura = { anomes_key: number; valor_pendente: number | null }
export type Saldo = { snapshot_date: string; account_type: string; balance: number | null }
export type Posicao = { snapshot_date: string; current_value: number | null }

export type TxResumo = { id: string; date: string; name: string; value: string; account: string }
export type Categoria = {
  id: string
  nome: string
  total: number
  valor: string
  share: string
  width: string
  count: string
  txs: TxResumo[]
}
export type ResumoMes = {
  entrou: number
  saiu: number
  sobrou: number
  fatura: number | null
  naConta: number | null
  investido: number | null
  categorias: Categoria[]
}

const MESES = [
  'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]

const moeda = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })

export function meses(txs: Tx[]): string[] {
  return [...new Set(txs.map(t => t.date.slice(0, 7)))].sort()
}

export function rotuloMes(mes: string): string {
  return `${MESES[Number(mes.slice(5, 7)) - 1]} de ${mes.slice(0, 4)}`
}

export function brl(v: number | null): string {
  return v === null ? '—' : moeda.format(v)
}

export function brlSinal(v: number | null): string {
  return v !== null && v > 0 ? `+${brl(v)}` : brl(v)
}

function percentual(fracao: number): string {
  return new Intl.NumberFormat('pt-BR', {
    style: 'percent',
    maximumFractionDigits: fracao < 0.1 ? 1 : 0,
  }).format(fracao)
}

const soma = (xs: number[]) => xs.reduce((a, b) => a + b, 0)

// Nulo é "sem valor", não zero. Sem nenhum valor, o resultado é null e a tela mostra —.
function somaOuNulo(xs: (number | null)[]): number | null {
  const valores = xs.filter((x): x is number => x !== null)
  return valores.length ? soma(valores) : null
}

function ultimoSnapshot<T extends { snapshot_date: string }>(linhas: T[]): T[] {
  if (linhas.length === 0) return []
  const ultimo = linhas.reduce((m, l) => (l.snapshot_date > m ? l.snapshot_date : m), '')
  return linhas.filter(l => l.snapshot_date === ultimo)
}

function conta(tipo: string): string {
  return tipo === 'BANK' ? 'Conta' : tipo === 'CREDIT' ? 'Cartão' : tipo
}

export function resumirMes(
  mes: string,
  txs: Tx[],
  faturas: Fatura[],
  saldos: Saldo[],
  posicoes: Posicao[],
  rotulos: Record<string, string>,
): ResumoMes {
  const doMes = txs.filter(t => t.date.slice(0, 7) === mes)
  const entrou = soma(doMes.filter(t => t.amount_signed > 0).map(t => t.amount_signed))
  const saiu = soma(doMes.filter(t => t.amount_signed < 0).map(t => -t.amount_signed))

  const grupos = new Map<string, Tx[]>()
  for (const t of doMes.filter(t => t.amount_signed < 0)) {
    const nome = t.category === null ? 'Sem categoria' : (rotulos[t.category] ?? t.category)
    grupos.set(nome, [...(grupos.get(nome) ?? []), t])
  }
  const base = [...grupos].map(([nome, lista]) => ({
    nome, lista, total: soma(lista.map(t => -t.amount_signed)),
  }))
  base.sort((a, b) => b.total - a.total || a.nome.localeCompare(b.nome, 'pt-BR'))
  const maior = base.length ? base[0].total : 0

  const categorias: Categoria[] = base.map(({ nome, lista, total }) => ({
    id: nome,
    nome,
    total,
    valor: brl(total),
    share: percentual(total / saiu),
    width: `${Math.max(1.5, (total / maior) * 100).toFixed(1)}%`,
    count: lista.length === 1 ? '1 transação' : `${lista.length} transações`,
    txs: [...lista]
      .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .map(t => ({
        id: t.id,
        date: `${t.date.slice(8, 10)}/${t.date.slice(5, 7)}`,
        name: t.etl_merchant_key ?? '—',
        value: brl(-t.amount_signed),
        account: conta(t.account_type),
      })),
  }))

  const chave = Number(mes.replace('-', ''))
  const daFatura = faturas.filter(f => f.anomes_key === chave)
  const bancarios = ultimoSnapshot(saldos.filter(s => s.account_type === 'BANK'))
  const posicoesAtuais = ultimoSnapshot(posicoes)

  return {
    entrou,
    saiu,
    sobrou: entrou - saiu,
    fatura: somaOuNulo(daFatura.map(f => f.valor_pendente)),
    naConta: somaOuNulo(bancarios.map(s => s.balance)),
    investido: somaOuNulo(posicoesAtuais.map(p => p.current_value)),
    categorias,
  }
}
