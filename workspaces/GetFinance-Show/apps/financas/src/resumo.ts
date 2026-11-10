// Resumo do mês a partir das tabelas que o ETL calcula (df-cns etapa 1). Este módulo só escolhe as
// linhas do mês e formata: as regras (classificação, base caixa, próxima fatura) estão no ETL.
// Datas são texto YYYY-MM-DD: nunca vão para Date.

export type SaudeMes = { anomes_key: number; entradas: number; saidas_total: number; sobra: number }
export type SaudeCategoria = { anomes_key: number; grupo: string; categoria: string; valor: number }
export type TxClassificada = {
  id: string
  date: string
  anomes_key: number
  account_type: string
  etl_merchant_key: string | null
  amount_signed: number
  categoria: string
  Tipo: string
  cash_flag: boolean
}
export type Liquidez = { anomes_key: number; caixa_saldo: number | null }
export type Investimentos = { anomes_key: number; posicao: number | null }
export type Cartao = { anomes_key: number; proxima_fatura: number | null }

// null = a tabela não existe no workspace (a API responde 404).
export type Dados = {
  saude: SaudeMes[] | null
  saudeCategoria: SaudeCategoria[] | null
  transacoes: TxClassificada[] | null
  liquidez: Liquidez[] | null
  investimentos: Investimentos[] | null
  cartao: Cartao[] | null
}

export type TxResumo = { id: string; date: string; name: string; value: string; account: string }
export type Categoria = {
  id: string
  nome: string
  grupo: string
  total: number
  valor: string
  share: string
  width: string
  count: string
  txs: TxResumo[]
}
export type ResumoMes = {
  entrou: number | null
  saiu: number | null
  sobrou: number | null
  fatura: number | null
  naConta: number | null
  investido: number | null
  categorias: Categoria[]
}

// O grupo das entradas no report_saude_categoria; os demais são os Tipos de saída.
const GRUPO_ENTRADAS = 'Entradas'

const MESES = [
  'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
  'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]

const moeda = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })

const paraMes = (anomes: number) => `${Math.floor(anomes / 100)}-${String(anomes % 100).padStart(2, '0')}`
const paraAnomes = (mes: string) => Number(mes.replace('-', ''))

export function meses(dados: Dados): string[] {
  return [...new Set((dados.saude ?? []).map(s => paraMes(s.anomes_key)))].sort()
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

function conta(tipo: string): string {
  return tipo === 'BANK' ? 'Conta' : tipo === 'CREDIT' ? 'Cartão' : tipo
}

function doMes<T extends { anomes_key: number }>(linhas: T[] | null, anomes: number): T | null {
  return linhas?.find(l => l.anomes_key === anomes) ?? null
}

function transacoesDaCategoria(dados: Dados, anomes: number, grupo: string, nome: string): TxResumo[] {
  // O mesmo recorte do report_saude_categoria: linhas de caixa do mês, do Tipo e da categoria.
  return (dados.transacoes ?? [])
    .filter(t => t.anomes_key === anomes && t.cash_flag && t.Tipo === grupo && t.categoria === nome)
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .map(t => ({
      id: t.id,
      date: `${t.date.slice(8, 10)}/${t.date.slice(5, 7)}`,
      name: t.etl_merchant_key ?? '—',
      value: brl(-t.amount_signed),
      account: conta(t.account_type),
    }))
}

export function resumirMes(mes: string, dados: Dados): ResumoMes {
  const anomes = paraAnomes(mes)
  const saude = doMes(dados.saude, anomes)

  const saidas = (dados.saudeCategoria ?? [])
    .filter(c => c.anomes_key === anomes && c.grupo !== GRUPO_ENTRADAS)
    .sort((a, b) => b.valor - a.valor || a.categoria.localeCompare(b.categoria, 'pt-BR'))
  const maior = saidas.length ? saidas[0].valor : 0
  const total = saude?.saidas_total ?? 0

  const categorias: Categoria[] = saidas.map(c => {
    const txs = transacoesDaCategoria(dados, anomes, c.grupo, c.categoria)
    return {
      id: `${c.grupo}|${c.categoria}`,
      nome: c.categoria,
      grupo: c.grupo,
      total: c.valor,
      valor: brl(c.valor),
      share: total ? percentual(c.valor / total) : '—',
      width: `${Math.max(1.5, maior > 0 ? (c.valor / maior) * 100 : 0).toFixed(1)}%`,
      count: txs.length === 1 ? '1 transação' : `${txs.length} transações`,
      txs,
    }
  })

  return {
    entrou: saude?.entradas ?? null,
    saiu: saude?.saidas_total ?? null,
    sobrou: saude?.sobra ?? null,
    fatura: doMes(dados.cartao, anomes)?.proxima_fatura ?? null,
    naConta: doMes(dados.liquidez, anomes)?.caixa_saldo ?? null,
    investido: doMes(dados.investimentos, anomes)?.posicao ?? null,
    categorias,
  }
}
