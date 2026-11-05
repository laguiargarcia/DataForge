import { describe, expect, test } from 'vitest'
import {
  brl, brlSinal, meses, resumirMes, rotuloMes,
  type Fatura, type Posicao, type Saldo, type Tx,
} from './resumo'

const rotulos = { Groceries: 'Mercado', Health: 'Saúde', Other: 'Outra' }

function tx(p: Partial<Tx> & { id: string }): Tx {
  return {
    date: '2026-06-10', account_type: 'BANK', etl_merchant_key: 'Loja',
    amount_signed: -1, category: 'Groceries', ...p,
  }
}

const txsJun: Tx[] = [
  tx({ id: 'a', amount_signed: 100, category: 'Income', etl_merchant_key: 'Salário' }),
  tx({ id: 'b', amount_signed: -12, category: 'Groceries', date: '2026-06-03', etl_merchant_key: 'Mercadinho' }),
  tx({ id: 'c', amount_signed: -8, category: 'Groceries', date: '2026-06-20', etl_merchant_key: 'Feira' }),
  tx({ id: 'd', amount_signed: -8, category: 'Health', account_type: 'CREDIT', etl_merchant_key: 'Farmácia' }),
  tx({ id: 'e', amount_signed: -2, category: 'Other', account_type: 'OUTRO', etl_merchant_key: null }),
  tx({ id: 'f', amount_signed: -50, category: 'Groceries', date: '2026-05-31' }),
]
const faturas: Fatura[] = [
  { anomes_key: 202606, valor_pendente: 10 },
  { anomes_key: 202606, valor_pendente: 2.5 },
  { anomes_key: 202605, valor_pendente: 99 },
]
const saldos: Saldo[] = [
  { snapshot_date: '2026-06-01', account_type: 'BANK', balance: 1 },
  { snapshot_date: '2026-06-28', account_type: 'BANK', balance: 300 },
  { snapshot_date: '2026-06-28', account_type: 'BANK', balance: 200 },
  { snapshot_date: '2026-06-28', account_type: 'CREDIT', balance: -999 },
]
const posicoes: Posicao[] = [
  { snapshot_date: '2026-06-01', current_value: 1 },
  { snapshot_date: '2026-06-28', current_value: 1000 },
  { snapshot_date: '2026-06-28', current_value: 234.5 },
]

const resumo202606 = () => resumirMes('2026-06', txsJun, faturas, saldos, posicoes, rotulos)
const resumoCom = (p: Partial<Tx>) =>
  resumirMes('2026-06', [tx({ id: 'x', ...p })], [], [], [], rotulos)
const resumoSemFatura = () => resumirMes('2026-07', txsJun, faturas, saldos, posicoes, rotulos)
const nbsp = (s: string) => s.replace(/\s/g, ' ')

describe('resumirMes', () => {
  test('entrou, saiu e sobrou', () => {
    const r = resumo202606()
    expect(r.entrou).toBeCloseTo(100); expect(r.saiu).toBeCloseTo(30); expect(r.sobrou).toBeCloseTo(70)
  })
  test('sobrou negativo sem mais e zero sem sinal', () => {
    expect(brlSinal(-309.97)).toBe(brl(-309.97)); expect(brlSinal(0)).toBe(brl(0))
    expect(nbsp(brlSinal(5705.02))).toBe('+R$ 5.705,02')
  })
  test('categorias da maior para a menor, só saídas, com rótulo', () => {
    expect(resumo202606().categorias.map(c => c.nome)).toEqual(['Mercado', 'Saúde', 'Outra'])
  })
  test('categoria nula vira Sem categoria', () => {
    const r = resumoCom({ category: null })
    expect(r.categorias[0].nome).toBe('Sem categoria')
  })
  test('share e count seguem o artboard', () => {
    const [m, s, o] = resumo202606().categorias
    expect(m.share).toBe('67%')
    expect(s.share).toBe('27%')
    expect(o.share).toBe('6,7%')
    expect(m.count).toBe('2 transações')
    expect(o.count).toBe('1 transação')
    expect(nbsp(m.valor)).toBe('R$ 20,00')
    expect(m.total).toBeCloseTo(20)
  })
  test('width mínima de 1,5% e a maior em 100%', () => {
    const [m] = resumo202606().categorias
    expect(m.width).toBe('100.0%')
    const r = resumirMes('2026-06', [
      tx({ id: '1', amount_signed: -1000 }),
      tx({ id: '2', amount_signed: -1, category: 'Health' }),
    ], [], [], [], rotulos)
    expect(r.categorias[1].width).toBe('1.5%')
  })
  test('transações em data decrescente, nome de etl_merchant_key, Conta ou Cartão', () => {
    const [m, s, o] = resumo202606().categorias
    expect(m.txs.map(t => t.id)).toEqual(['c', 'b'])
    expect(m.txs.map(t => t.date)).toEqual(['20/06', '03/06'])
    expect(m.txs[0].name).toBe('Feira')
    expect(nbsp(m.txs[0].value)).toBe('R$ 8,00')
    expect(m.txs[0].account).toBe('Conta')   // BANK > Conta
    expect(s.txs[0].account).toBe('Cartão')  // CREDIT > Cartão
    expect(o.txs[0].account).toBe('OUTRO')   // outro > como veio
    expect(o.txs[0].name).toBe('—')          // nulo > —
  })
  test('empate de data desempata por id', () => {
    const r = resumirMes('2026-06', [
      tx({ id: 'z' }), tx({ id: 'a' }),
    ], [], [], [], rotulos)
    expect(r.categorias[0].txs.map(t => t.id)).toEqual(['a', 'z'])
  })
  test('data sem deslocamento de fuso', () => {
    expect(resumoCom({ date: '2026-06-01' }).categorias[0].txs[0].date).toBe('01/06')
  })
  test('fatura: soma do mês; zero é R$ 0,00; mês sem linha é null', () => {
    expect(resumo202606().fatura).toBeCloseTo(12.5)
    expect(resumoSemFatura().fatura).toBeNull()
    expect(nbsp(brl(0))).toBe('R$ 0,00')
  })
  test('naConta e investido usam só o último snapshot e não mudam com o mês', () => {
    const r = resumo202606()
    expect(r.naConta).toBeCloseTo(500)
    expect(r.investido).toBeCloseTo(1234.5)
    const j = resumoSemFatura()
    expect(j.naConta).toBeCloseTo(500); expect(j.investido).toBeCloseTo(1234.5)
    const sem = resumirMes('2026-06', txsJun, faturas, [saldos[3]], [], rotulos)
    expect(sem.naConta).toBeNull(); expect(sem.investido).toBeNull()
  })
  test('fatura ignora nulos; todos nulos é null, não R$ 0,00', () => {
    const parcial: Fatura[] = [
      { anomes_key: 202606, valor_pendente: null },
      { anomes_key: 202606, valor_pendente: 2.5 },
    ]
    expect(resumirMes('2026-06', txsJun, parcial, saldos, posicoes, rotulos).fatura).toBeCloseTo(2.5)
    const nulas: Fatura[] = [{ anomes_key: 202606, valor_pendente: null }]
    const r = resumirMes('2026-06', txsJun, nulas, saldos, posicoes, rotulos)
    expect(r.fatura).toBeNull()
    expect(brl(r.fatura)).toBe('—')
  })
  test('naConta ignora nulos; todos nulos é null, não R$ 0,00', () => {
    const parcial: Saldo[] = [
      { snapshot_date: '2026-06-20', account_type: 'BANK', balance: null },
      { snapshot_date: '2026-06-20', account_type: 'BANK', balance: 50 },
    ]
    expect(resumirMes('2026-06', txsJun, faturas, parcial, posicoes, rotulos).naConta).toBeCloseTo(50)
    const nulos: Saldo[] = [
      { snapshot_date: '2026-06-20', account_type: 'BANK', balance: null },
      { snapshot_date: '2026-06-10', account_type: 'BANK', balance: 7 },
    ]
    const r = resumirMes('2026-06', txsJun, faturas, nulos, posicoes, rotulos)
    expect(r.naConta).toBeNull()
    expect(brl(r.naConta)).toBe('—')
  })
  test('investido ignora nulos; todos nulos é null, não R$ 0,00', () => {
    const parcial: Posicao[] = [
      { snapshot_date: '2026-06-20', current_value: null },
      { snapshot_date: '2026-06-20', current_value: 30 },
    ]
    expect(resumirMes('2026-06', txsJun, faturas, saldos, parcial, rotulos).investido).toBeCloseTo(30)
    const nulas: Posicao[] = [{ snapshot_date: '2026-06-20', current_value: null }]
    const r = resumirMes('2026-06', txsJun, faturas, saldos, nulas, rotulos)
    expect(r.investido).toBeNull()
    expect(brl(r.investido)).toBe('—')
  })
  test('naConta pega o último snapshot só entre linhas BANK, mesmo com CREDIT mais novo', () => {
    const s: Saldo[] = [
      { snapshot_date: '2026-06-20', account_type: 'BANK', balance: 150 },
      { snapshot_date: '2026-06-20', account_type: 'BANK', balance: 50 },
      { snapshot_date: '2026-06-10', account_type: 'BANK', balance: 7 },
      { snapshot_date: '2026-06-30', account_type: 'CREDIT', balance: -500 },
    ]
    expect(resumirMes('2026-06', txsJun, faturas, s, posicoes, rotulos).naConta).toBeCloseTo(200)
  })
})

describe('formatação', () => {
  test('meses e rótulo', () => {
    const txsDezAJun = [
      tx({ id: '1', date: '2026-06-02' }), tx({ id: '2', date: '2025-12-31' }),
      tx({ id: '3', date: '2026-01-15' }), tx({ id: '4', date: '2026-06-30' }),
    ]
    expect(meses(txsDezAJun)).toEqual(['2025-12', '2026-01', '2026-06'])
    expect(rotuloMes('2026-06')).toBe('junho de 2026'); expect(rotuloMes('2026-03')).toBe('março de 2026')
  })
  test('brl nulo é travessão', () => {
    expect(brl(null)).toBe('—'); expect(brlSinal(null)).toBe('—')
  })
})
