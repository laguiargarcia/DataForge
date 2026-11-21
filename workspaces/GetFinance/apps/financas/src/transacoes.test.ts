import { expect, test } from 'vitest'
import type { TxClassificada } from './resumo'
import {
  SEM_CATEGORIA, categoriasDe, correcoesPorId, dataCurta, estadoCorrecao, filtrar, marcacaoAutomatica, mesesDe, orfas,
  rotuloAnomes, textoConfirmacao, textoContagem, textoEstado,
} from './transacoes'

function tx(p: Partial<TxClassificada> & { id: string }): TxClassificada {
  return {
    date: '2026-06-10', anomes_key: 202606, account_type: 'BANK', etl_merchant_key: null, amount_signed: -10,
    categoria: 'Mercado', Tipo: 'Essencial', cash_flag: true, description_normalized: 'PADARIA',
    subcategoria: 'Groceries', etl_tag: null, corrigida: false, ...p,
  }
}

const TXS = [
  tx({ id: 'a', date: '2026-05-03', anomes_key: 202605, description_normalized: 'AÇOUGUE CENTRAL' }),
  tx({ id: 'b', date: '2026-06-20', description_normalized: 'UBER TRIP', categoria: 'Transporte' }),
  tx({ id: 'c', date: '2026-06-20', description_normalized: 'PIX', categoria: SEM_CATEGORIA, subcategoria: SEM_CATEGORIA }),
  tx({ id: 'd', date: '2026-06-01', description_normalized: null, etl_merchant_key: 'Padaria Pão' }),
]
const ids = (txs: TxClassificada[]) => txs.map(t => t.id)
const todos = { busca: '', mes: null, categoria: null }

test('sem filtro: todas, da mais recente para a mais antiga (empate pelo id)', () => {
  expect(ids(filtrar(TXS, todos))).toEqual(['b', 'c', 'd', 'a'])
})

test('busca ignora maiúsculas e acentos e olha também o estabelecimento', () => {
  expect(ids(filtrar(TXS, { ...todos, busca: 'acougue' }))).toEqual(['a'])
  expect(ids(filtrar(TXS, { ...todos, busca: '  uber ' }))).toEqual(['b'])
  expect(ids(filtrar(TXS, { ...todos, busca: 'pão' }))).toEqual(['d'])
  expect(ids(filtrar(TXS, { ...todos, busca: 'zzz' }))).toEqual([])
})

test('filtro por mês e por categoria, combinados com a busca', () => {
  expect(ids(filtrar(TXS, { ...todos, mes: 202605 }))).toEqual(['a'])
  expect(ids(filtrar(TXS, { ...todos, categoria: SEM_CATEGORIA }))).toEqual(['c'])
  expect(ids(filtrar(TXS, { busca: 'p', mes: 202606, categoria: 'Mercado' }))).toEqual(['d'])
})

test('filtrar não mexe na lista de entrada', () => {
  const copia = [...TXS]
  filtrar(TXS, todos)
  expect(TXS).toEqual(copia)
})

test('meses do mais recente ao mais antigo, e o rótulo', () => {
  expect(mesesDe(TXS)).toEqual([202606, 202605])
  expect(rotuloAnomes(202605)).toBe('maio de 2026')
  expect(rotuloAnomes(202512)).toBe('dezembro de 2025')
})

test('categorias com "(sem categoria)" primeiro, o resto em ordem alfabética', () => {
  expect(categoriasDe(TXS)).toEqual([SEM_CATEGORIA, 'Mercado', 'Transporte'])
  expect(categoriasDe([TXS[1], TXS[0]])).toEqual(['Mercado', 'Transporte'])
})

test('data curta', () => { expect(dataCurta('2026-06-01')).toBe('01/06/2026') })

test('marcação automática em palavras', () => {
  expect(marcacaoAutomatica('investment')).toBe('investimento')
  expect(marcacaoAutomatica('card_payment_bank')).toBe('pagamento de cartão')
  expect(marcacaoAutomatica('card_payment_card')).toBe('pagamento de cartão')
  expect(marcacaoAutomatica(null)).toBeNull()
  expect(marcacaoAutomatica('outra')).toBeNull()
})

test('confirmação só quando a correção sobrepõe uma marcação automática', () => {
  expect(textoConfirmacao(tx({ id: 'x' }), 'Contas')).toBeNull()
  expect(textoConfirmacao(tx({ id: 'x', etl_tag: 'investment' }), 'Groceries')).toBe(
    'Esta transação foi marcada automaticamente como investimento. A correção passa por cima dessa marcação: ' +
    'depois do Aplicar ela deixa de contar como investimento e passa a valer como «Groceries». Continuar?')
})

test('estado da correção: banco e curated podem estar desencontrados até o Aplicar', () => {
  const banco = correcoesPorId([{ transacao_id: 'x', subcategoria: 'Contas', encontrada: true }])
  expect(estadoCorrecao(tx({ id: 'y' }), banco)).toEqual({ tipo: 'nenhuma' })
  expect(estadoCorrecao(tx({ id: 'x', corrigida: true, subcategoria: 'Contas' }), banco)).toEqual({ tipo: 'aplicada', subcategoria: 'Contas' })
  expect(estadoCorrecao(tx({ id: 'x' }), banco)).toEqual({ tipo: 'a_aplicar', subcategoria: 'Contas' })
  // trocada de novo no banco depois do último Aplicar
  expect(estadoCorrecao(tx({ id: 'x', corrigida: true, subcategoria: 'Groceries' }), banco)).toEqual({ tipo: 'a_aplicar', subcategoria: 'Contas' })
  expect(estadoCorrecao(tx({ id: 'y', corrigida: true }), banco)).toEqual({ tipo: 'desfeita_a_aplicar' })
})

test('texto de cada estado', () => {
  expect(textoEstado({ tipo: 'nenhuma' })).toBeNull()
  expect(textoEstado({ tipo: 'aplicada', subcategoria: 'Contas' })).toBe('Corrigida à mão')
  expect(textoEstado({ tipo: 'a_aplicar', subcategoria: 'Contas' })).toBe('Corrigida para «Contas» (falta Aplicar)')
  expect(textoEstado({ tipo: 'desfeita_a_aplicar' })).toBe('Correção desfeita (falta Aplicar)')
})

test('órfãs são as correções sem transação', () => {
  const c = [{ transacao_id: 'x', subcategoria: 'A', encontrada: true }, { transacao_id: 'z', subcategoria: 'B', encontrada: false }]
  expect(orfas(c)).toEqual([c[1]])
})

test('texto da contagem, com o limite de linhas', () => {
  expect(textoContagem(0)).toBe('0 transações')
  expect(textoContagem(1)).toBe('1 transação')
  expect(textoContagem(300)).toBe('300 transações')
  expect(textoContagem(301)).toBe('Mostrando 300 de 301 transações; refine a busca ou os filtros.')
})
