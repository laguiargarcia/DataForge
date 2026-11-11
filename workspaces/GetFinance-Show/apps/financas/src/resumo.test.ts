import { describe, expect, test } from 'vitest'
import {
  brl, brlSinal, meses, resumirMes, rotuloMes,
  type Dados, type SaudeCategoria, type TxClassificada,
} from './resumo'

function tx(p: Partial<TxClassificada> & { id: string }): TxClassificada {
  return {
    date: '2026-06-10', anomes_key: 202606, account_type: 'BANK', etl_merchant_key: 'Loja',
    amount_signed: -1, categoria: 'Mercado', Tipo: 'Essencial', cash_flag: true, ...p,
  }
}

const cat = (grupo: string, categoria: string, valor: number, anomes_key = 202606): SaudeCategoria =>
  ({ anomes_key, grupo, categoria, valor })

const dados: Dados = {
  saude: [
    { anomes_key: 202605, entradas: 50, saidas_total: 80, sobra: -30 },
    { anomes_key: 202606, entradas: 1000, saidas_total: 400, sobra: 600 },
  ],
  saudeCategoria: [
    cat('Entradas', 'Salário', 1000),
    cat('Essencial', 'Mercado', 20),
    cat('Não Essencial', 'Compras', 180),
    cat('Pagamento cartão', 'Pagamento cartão', 200),
    cat('Essencial', 'Mercado', 80, 202605),
  ],
  transacoes: [
    tx({ id: 'b', amount_signed: -12, date: '2026-06-03', etl_merchant_key: 'Mercadinho' }),
    tx({ id: 'c', amount_signed: -8, date: '2026-06-20', etl_merchant_key: 'Feira' }),
    tx({ id: 'd', amount_signed: -8, account_type: 'CREDIT', cash_flag: false }),
    tx({ id: 'e', amount_signed: -5, categoria: 'Mercado', Tipo: 'Não Essencial' }),
    tx({ id: 'f', amount_signed: -50, date: '2026-05-31', anomes_key: 202605 }),
    tx({ id: 'g', amount_signed: -180, categoria: 'Compras', Tipo: 'Não Essencial', etl_merchant_key: null }),
  ],
  liquidez: [{ anomes_key: 202606, caixa_saldo: 5230.55 }, { anomes_key: 202605, caixa_saldo: null }],
  investimentos: [{ anomes_key: 202606, posicao: 1234.5 }],
  cartao: [{ anomes_key: 202606, proxima_fatura: 0 }],
}

const nbsp = (s: string) => s.replace(/\s/g, ' ')
const jun = () => resumirMes('2026-06', dados)

describe('resumirMes', () => {
  test('entrou, saiu e sobrou vêm do report_saude', () => {
    const r = jun()
    expect([r.entrou, r.saiu, r.sobrou]).toEqual([1000, 400, 600])
    expect(resumirMes('2026-05', dados).sobrou).toBe(-30)
  })
  test('mês sem linha no report_saude: tudo null', () => {
    const r = resumirMes('2026-07', dados)
    expect([r.entrou, r.saiu, r.sobrou, r.naConta, r.investido, r.fatura]).toEqual([null, null, null, null, null, null])
    expect(r.categorias).toEqual([])
  })
  test('faixas: caixa, posição e próxima fatura do mês; zero é zero, nulo é null', () => {
    const r = jun()
    expect([r.naConta, r.investido, r.fatura]).toEqual([5230.55, 1234.5, 0])
    expect(resumirMes('2026-05', dados).naConta).toBeNull()
  })
  test('categorias só das saídas, maior primeiro, com o grupo', () => {
    const r = jun()
    expect(r.categorias.map(c => [c.grupo, c.nome])).toEqual([
      ['Pagamento cartão', 'Pagamento cartão'], ['Não Essencial', 'Compras'], ['Essencial', 'Mercado'],
    ])
  })
  test('share sobre o que saiu, width relativa à maior com mínimo de 1,5%', () => {
    const [maior, , menor] = jun().categorias
    expect(nbsp(maior.share)).toBe('50%')
    expect(maior.width).toBe('100.0%')
    expect(menor.width).toBe('10.0%')
    const minima = resumirMes('2026-06', { ...dados, saudeCategoria: [cat('Essencial', 'A', 1000), cat('Essencial', 'B', 1)] })
    expect(minima.categorias[1].width).toBe('1.5%')
  })
  test('transações da categoria: mesmo mês, caixa e Tipo, data decrescente, desempate por id', () => {
    const mercado = jun().categorias.find(c => c.nome === 'Mercado')!
    expect(mercado.txs.map(t => t.id)).toEqual(['c', 'b'])
    expect(mercado.count).toBe('2 transações')
    expect(mercado.txs[0]).toMatchObject({ date: '20/06', name: 'Feira', account: 'Conta' })
    expect(nbsp(mercado.txs[0].value)).toBe('R$ 8,00')
  })
  test('nome vazio vira —; uma transação no singular', () => {
    const compras = jun().categorias.find(c => c.nome === 'Compras')!
    expect(compras.txs[0].name).toBe('—')
    expect(compras.count).toBe('1 transação')
  })
  test('id da categoria separa o mesmo nome em grupos diferentes', () => {
    const r = resumirMes('2026-06', { ...dados, saudeCategoria: [cat('Essencial', 'X', 2), cat('Não Essencial', 'X', 1)] })
    expect(new Set(r.categorias.map(c => c.id)).size).toBe(2)
  })
  test('tabela ausente (null) não quebra: só os valores dela viram null', () => {
    const r = resumirMes('2026-06', { ...dados, liquidez: null, cartao: null })
    expect([r.naConta, r.fatura, r.entrou]).toEqual([null, null, 1000])
  })
})

describe('meses e formatação', () => {
  test('meses vêm do report_saude, em ordem', () => {
    expect(meses(dados)).toEqual(['2026-05', '2026-06'])
    expect(meses({ ...dados, saude: null })).toEqual([])
  })
  test('rótulo do mês', () => {
    expect(rotuloMes('2026-06')).toBe('junho de 2026')
  })
  test('brl nulo é travessão; sinal só no positivo', () => {
    expect(brl(null)).toBe('—')
    expect(nbsp(brlSinal(5705.02))).toBe('+R$ 5.705,02')
    expect(brlSinal(-309.97)).toBe(brl(-309.97))
    expect(brlSinal(0)).toBe(brl(0))
  })
})
