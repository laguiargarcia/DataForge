import { describe, expect, it } from 'vitest'
import { brl, deslocar, diaMes, mesDe, mesValido, rotuloMes, tipoConta } from './formato'

// Intl separa "R$" do número com NBSP.
const semNbsp = (texto: string) => texto.replaceAll(' ', ' ')

describe('brl', () => {
  it('mostra os centavos, com sinal e milhar', () => {
    expect(semNbsp(brl(-1234.5))).toBe('-R$ 1.234,50')
    expect(semNbsp(brl(0.1 + 0.2))).toBe('R$ 0,30')
    expect(semNbsp(brl(6500))).toBe('R$ 6.500,00')
  })
})

describe('meses', () => {
  it('desloca através da virada do ano', () => {
    expect(deslocar('2026-01', -1)).toBe('2025-12')
    expect(deslocar('2025-12', 1)).toBe('2026-01')
    expect(deslocar('2026-06', -18)).toBe('2024-12')
    expect(deslocar('2026-06', 0)).toBe('2026-06')
  })

  it('aceita só AAAA-MM', () => {
    expect(mesValido('2026-06')).toBe(true)
    for (const ruim of [null, '0000-13', '0000-0', 'junho', '']) expect(mesValido(ruim)).toBe(false)
  })

  it('dá o nome do mês e o mês de uma data', () => {
    expect(rotuloMes('2026-06')).toBe('junho de 2026')
    expect(rotuloMes('2025-12')).toBe('dezembro de 2025')
    expect(mesDe(new Date(2026, 10, 24))).toBe('2026-11')
  })

  it('mostra dia e mês de uma data da tabela', () => {
    expect(diaMes('2026-06-30')).toBe('30/06')
  })
})

describe('tipoConta', () => {
  it('traduz os tipos da tabela e deixa um desconhecido como veio', () => {
    expect([tipoConta('BANK'), tipoConta('CREDIT'), tipoConta('OUTRO')]).toEqual(['Conta', 'Cartão', 'OUTRO'])
  })
})
