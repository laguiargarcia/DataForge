import { describe, expect, it } from 'vitest'
import type { Transacao } from './api'
import { falta } from './Linha'

const t = (subcategoria: string, corrigida: boolean): Transacao => ({
  id: 'a', date: '2026-06-30', anomes_key: 202606, description_normalized: 'Mercado Central',
  amount_signed: -10, account_type: 'Conta', categoria: 'Essencial', subcategoria, corrigida,
})

describe('falta', () => {
  it('uma correção ainda não publicada mostra a categoria nova', () => {
    expect(falta(t('Mercado', false), { transacao_id: 'a', subcategoria: 'Saúde' })).toBe('Saúde')
    // A mesma categoria que as regras já dão ainda tem de ser aplicada para virar corrigida.
    expect(falta(t('Mercado', false), { transacao_id: 'a', subcategoria: 'Mercado' })).toBe('Mercado')
  })

  it('uma correção publicada não falta', () => {
    expect(falta(t('Saúde', true), { transacao_id: 'a', subcategoria: 'Saúde' })).toBeNull()
    expect(falta(t('Mercado', false), undefined)).toBeNull()
  })

  it('uma correção desfeita falta até o aplicar', () => {
    expect(falta(t('Saúde', true), undefined)).toBe('volta à categoria das regras')
  })
})
