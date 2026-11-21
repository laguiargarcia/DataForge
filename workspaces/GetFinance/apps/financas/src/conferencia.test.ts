import { describe, expect, test } from 'vitest'
import {
  montarConferencia, rotuloInstante,
  type LinhaConferencia, type LinhaSuspeito,
} from './conferencia'

// A API serializa os instantes (Datetime UTC) como ISO com '+00:00' e os dias como YYYY-MM-DD.
const H = (dia: string, hora = '12:00:00') => `${dia}T${hora}+00:00`

function intervalo(p: Partial<LinhaConferencia> & { ate: string }): LinhaConferencia {
  return {
    account_uuid: 'conta-a', de: H('2026-09-01'), coletado_em: p.ate.slice(0, 10),
    saldo_de: 100, saldo_ate: 100, variacao_saldo: 0, soma_lancamentos: 0, n_lancamentos: 0,
    diferenca: 0, diferenca_acumulada: 0, estado: 'fecha', explicacao: null, ...p,
  }
}

function suspeito(p: Partial<LinhaSuspeito> & { transaction_id: string }): LinhaSuspeito {
  return {
    account_uuid: 'conta-a', date: '2026-09-05', amount: -10, description: 'Loja', status: 'POSTED',
    motivo: 'sumiu_da_coleta', primeira_coleta: '2026-09-05', ultima_coleta: '2026-09-06', n_coletas: 2,
    intervalo_de: H('2026-09-04'), intervalo_ate: H('2026-09-06'), intervalo_estado: 'nao_fecha', ...p,
  }
}

const nbsp = (s: string) => s.replace(/\s/g, ' ')

const linhas: LinhaConferencia[] = [
  intervalo({ de: H('2026-09-01'), ate: H('2026-09-02'), diferenca: 0.05, diferenca_acumulada: 0.05 }),
  intervalo({ de: H('2026-09-02'), ate: H('2026-09-04'), diferenca: -50, diferenca_acumulada: -49.95,
              estado: 'fecha_com_vizinho' }),
  intervalo({ de: H('2026-09-04'), ate: H('2026-09-06'), diferenca: 50, diferenca_acumulada: 0.05,
              estado: 'fecha_com_vizinho' }),
  intervalo({ de: H('2026-09-06'), ate: H('2026-09-08'), diferenca: -120, diferenca_acumulada: -119.95,
              estado: 'nao_fecha' }),
  intervalo({ de: H('2026-09-08'), ate: H('2026-09-10'), diferenca: 0, diferenca_acumulada: -119.95 }),
  intervalo({ de: H('2026-09-10'), ate: H('2026-09-12'), diferenca: 30, diferenca_acumulada: -89.95,
              estado: 'em_aberto' }),
  intervalo({ account_uuid: 'conta-b', de: H('2026-09-01'), ate: H('2026-09-03'), diferenca: 2.5,
              diferenca_acumulada: 2.5, estado: 'nao_fecha' }),
  intervalo({ account_uuid: 'conta-b', de: H('2026-09-03'), ate: H('2026-09-05'), diferenca: 0,
              diferenca_acumulada: 2.5 }),
]

describe('montarConferencia: intervalos', () => {
  test('os que não fecham primeiro (mais novo antes), depois os que fecham com o vizinho, depois os que fecham', () => {
    const r = montarConferencia(linhas, [])
    expect(r.intervalos.map(i => [i.conta, i.estado, i.ate])).toEqual([
      ['conta-a', 'em_aberto', '12/09/2026 09:00'],
      ['conta-a', 'nao_fecha', '08/09/2026 09:00'],
      ['conta-b', 'nao_fecha', '03/09/2026 09:00'],
      ['conta-a', 'fecha_com_vizinho', '06/09/2026 09:00'],
      ['conta-a', 'fecha_com_vizinho', '04/09/2026 09:00'],
      ['conta-a', 'fecha', '10/09/2026 09:00'],
      ['conta-b', 'fecha', '05/09/2026 09:00'],
      ['conta-a', 'fecha', '02/09/2026 09:00'],
    ])
  })
  test('fecha_com_vizinho aparece distinto de fecha; só os que não fecham ficam marcados', () => {
    const r = montarConferencia(linhas, [])
    const rotulos = new Map(r.intervalos.map(i => [i.estado, [i.rotuloEstado, i.fecha]]))
    expect(Object.fromEntries(rotulos)).toEqual({
      em_aberto: ['Em aberto', false],
      nao_fecha: ['Não fecha', false],
      fecha_com_vizinho: ['Fecha com o vizinho', true],
      fecha: ['Fecha', true],
    })
  })
  test('divergência explicada: rótulo próprio, sem a marca de erro, com o motivo; só o que não fecha pode ser marcado', () => {
    const r = montarConferencia([
      intervalo({ ate: H('2026-05-28'), de: H('2026-05-26'), diferenca: 9.93, estado: 'explicado',
                  explicacao: 'seguro debitado antes da primeira coleta' }),
      intervalo({ ate: H('2026-06-01'), de: H('2026-05-28'), diferenca: -5, estado: 'nao_fecha' }),
      intervalo({ ate: H('2026-06-02'), de: H('2026-06-01'), estado: 'em_aberto', diferenca: 3 }),
      intervalo({ ate: H('2026-06-03'), de: H('2026-06-02') }),
    ], [])
    const por = new Map(r.intervalos.map(i => [i.estado, i]))
    const explicado = por.get('explicado')!
    expect([explicado.rotuloEstado, explicado.fecha, explicado.explicacao, explicado.podeExplicar]).toEqual(
      ['Explicado', true, 'seguro debitado antes da primeira coleta', false])
    expect(explicado.ateIso).toBe(H('2026-05-28'))
    expect([por.get('nao_fecha')!.podeExplicar, por.get('em_aberto')!.podeExplicar, por.get('fecha')!.podeExplicar])
      .toEqual([true, true, false])
    // Explicado vai junto dos que fecham com o vizinho, depois dos que não fecham.
    expect(r.intervalos.map(i => i.estado)).toEqual(['em_aberto', 'nao_fecha', 'explicado', 'fecha'])
  })
  test('estado desconhecido aparece como veio, entre os que não fecham', () => {
    const r = montarConferencia([...linhas, intervalo({ ate: H('2026-08-01'), de: H('2026-07-31'), estado: 'novo' })], [])
    const novo = r.intervalos.find(i => i.estado === 'novo')!
    expect([novo.rotuloEstado, novo.fecha]).toEqual(['novo', false])
    expect(r.intervalos.findIndex(i => i.estado === 'novo')).toBeLessThan(
      r.intervalos.findIndex(i => i.estado === 'fecha_com_vizinho'))
  })
  test('valores formatados: variação, soma com o número de lançamentos, diferença com sinal', () => {
    const r = montarConferencia([intervalo({
      ate: H('2026-09-02', '23:30:00'), de: H('2026-09-02', '02:15:00'),
      variacao_saldo: 1500.5, soma_lancamentos: 1380.5, n_lancamentos: 3, diferenca: 120,
    })], [])
    const [i] = r.intervalos
    expect(i.de).toBe('01/09/2026 23:15')
    expect(i.ate).toBe('02/09/2026 20:30')
    expect(nbsp(i.variacao)).toBe('R$ 1.500,50')
    expect(nbsp(i.soma)).toBe('R$ 1.380,50')
    expect(i.lancamentos).toBe('3 lançamentos')
    expect(nbsp(i.diferenca)).toBe('+R$ 120,00')
    const um = montarConferencia([intervalo({ ate: H('2026-09-02'), n_lancamentos: 1, diferenca: null })], [])
    expect(um.intervalos[0].lancamentos).toBe('1 lançamento')
    expect(um.intervalos[0].diferenca).toBe('—')
  })
  test('instante com fração de segundo ordena e formata como os outros', () => {
    const r = montarConferencia([
      intervalo({ ate: H('2026-09-02', '12:00:00'), estado: 'nao_fecha' }),
      intervalo({ ate: H('2026-09-02', '12:00:00.500000'), estado: 'nao_fecha' }),
    ], [])
    expect(r.intervalos.map(i => i.id)).toEqual([
      `conta-a|${H('2026-09-02', '12:00:00.500000')}`, `conta-a|${H('2026-09-02')}`,
    ])
    expect(rotuloInstante(H('2026-09-02', '12:00:00.500000'))).toBe('02/09/2026 09:00')
  })
})

describe('montarConferencia: diferença acumulada atual', () => {
  test('uma por conta: a do intervalo mais novo, com o estado dele', () => {
    const embaralhadas = [...linhas].reverse()
    const r = montarConferencia(embaralhadas, [])
    expect(r.contas.map(c => [c.conta, c.diferenca, c.ate, c.rotuloEstado])).toEqual([
      ['conta-a', -89.95, '12/09/2026 09:00', 'Em aberto'],
      ['conta-b', 2.5, '05/09/2026 09:00', 'Fecha'],
    ])
    expect(nbsp(r.contas[0].valor)).toBe('-R$ 89,95')
    expect(nbsp(r.contas[1].valor)).toBe('+R$ 2,50')
  })
  test('sem intervalos: nenhuma conta, nenhum aviso', () => {
    expect(montarConferencia([], [])).toEqual({ avisos: [], contas: [], intervalos: [], semIntervalo: [] })
  })
})

describe('montarConferencia: suspeitos', () => {
  const ate06 = H('2026-09-06')
  const ate08 = H('2026-09-08')
  test('cada suspeito vai para o intervalo em que cai, mais novo primeiro, desempate pelo id', () => {
    const r = montarConferencia(linhas, [
      suspeito({ transaction_id: 't2', date: '2026-09-05', intervalo_ate: ate06 }),
      suspeito({ transaction_id: 't1', date: '2026-09-05', intervalo_ate: ate06 }),
      suspeito({ transaction_id: 't3', date: '2026-09-07', intervalo_ate: ate06 }),
      suspeito({ transaction_id: 't4', date: '2026-09-07', intervalo_de: ate06, intervalo_ate: ate08 }),
    ])
    const doIntervalo = (ate: string) => r.intervalos.find(i => i.id === `conta-a|${ate}`)!
    expect(doIntervalo(ate06).suspeitos.map(s => s.id)).toEqual(['t3', 't1', 't2'])
    expect(doIntervalo(ate06).quantos).toBe('3 suspeitos')
    expect(doIntervalo(ate08).suspeitos.map(s => s.id)).toEqual(['t4'])
    expect(doIntervalo(ate08).quantos).toBe('1 suspeito')
    expect(r.intervalos.filter(i => i.suspeitos.length === 0).every(i => i.quantos === '')).toBe(true)
    expect(r.semIntervalo).toEqual([])
  })
  test('o intervalo é da conta: o mesmo instante em outra conta não mistura', () => {
    const r = montarConferencia(linhas, [
      suspeito({ transaction_id: 'b1', account_uuid: 'conta-b', intervalo_ate: H('2026-09-03') }),
    ])
    expect(r.intervalos.find(i => i.id === `conta-b|${H('2026-09-03')}`)!.suspeitos.map(s => s.id)).toEqual(['b1'])
  })
  test('vários motivos de um lançamento viram um suspeito só, motivos na ordem do ETL', () => {
    const r = montarConferencia(linhas, [
      suspeito({ transaction_id: 't1', motivo: 'uma_coleta_so' }),
      suspeito({ transaction_id: 't1', motivo: 'pendente_que_sumiu' }),
      suspeito({ transaction_id: 't1', motivo: 'sumiu_da_coleta' }),
      suspeito({ transaction_id: 't1', motivo: 'motivo_novo' }),
      suspeito({ transaction_id: 't1', motivo: 'aplicacao_automatica' }),
    ])
    const [s] = r.intervalos.find(i => i.id === `conta-a|${ate06}`)!.suspeitos
    expect(s.motivos).toEqual([
      'Sumiu da coleta', 'Pendente que sumiu', 'Aplicação automática', 'Veio numa coleta só', 'motivo_novo',
    ])
  })
  test('formata data, descrição e valor do suspeito', () => {
    const r = montarConferencia(linhas, [
      suspeito({ transaction_id: 't1', date: '2026-09-05', amount: -1234.5, description: 'Mercado' }),
      suspeito({ transaction_id: 't2', date: null, amount: null, description: null }),
    ])
    const [a, b] = r.intervalos.find(i => i.id === `conta-a|${ate06}`)!.suspeitos
    expect([a.data, a.descricao, nbsp(a.valor), a.conta]).toEqual(['05/09/2026', 'Mercado', '-R$ 1.234,50', 'conta-a'])
    expect([b.data, b.descricao, b.valor]).toEqual(['—', '—', '—'])
  })
  test('suspeito sem intervalo (cartão) e suspeito de intervalo que não está na conferência ficam à parte', () => {
    const r = montarConferencia(linhas, [
      suspeito({ transaction_id: 'c1', account_uuid: 'cartao', intervalo_de: null, intervalo_ate: null,
                 intervalo_estado: null, date: '2026-09-01' }),
      suspeito({ transaction_id: 'c2', intervalo_ate: H('2026-01-01'), date: '2026-01-01' }),
      suspeito({ transaction_id: 'c3', account_uuid: 'cartao', intervalo_de: null, intervalo_ate: null,
                 intervalo_estado: null, date: '2026-09-03', motivo: 'uma_coleta_so' }),
      suspeito({ transaction_id: 'c3', account_uuid: 'cartao', intervalo_de: null, intervalo_ate: null,
                 intervalo_estado: null, date: '2026-09-03', motivo: 'sumiu_da_coleta' }),
    ])
    expect(r.semIntervalo.map(s => [s.id, s.motivos.length])).toEqual([['c3', 2], ['c1', 1], ['c2', 1]])
    expect(r.intervalos.every(i => i.suspeitos.length === 0)).toBe(true)
  })
})

describe('montarConferencia: tabela ausente', () => {
  test('conferência ausente (null): aviso, e os suspeitos ficam à parte', () => {
    const r = montarConferencia(null, [suspeito({ transaction_id: 't1' })])
    expect(r.avisos).toEqual(['A conferência pelo saldo ainda não foi calculada neste workspace.'])
    expect([r.contas, r.intervalos]).toEqual([[], []])
    expect(r.semIntervalo.map(s => s.id)).toEqual(['t1'])
  })
  test('suspeitos ausentes (null): aviso, e os intervalos aparecem sem suspeitos', () => {
    const r = montarConferencia(linhas, null)
    expect(r.avisos).toEqual(['Os lançamentos suspeitos ainda não foram calculados neste workspace.'])
    expect(r.intervalos).toHaveLength(linhas.length)
    expect(r.semIntervalo).toEqual([])
  })
  test('as duas ausentes: dois avisos, tela vazia, sem erro', () => {
    const r = montarConferencia(null, null)
    expect(r.avisos).toHaveLength(2)
    expect([r.contas, r.intervalos, r.semIntervalo]).toEqual([[], [], []])
  })
})

describe('rotuloInstante', () => {
  test('horário de Brasília, nulo é travessão', () => {
    expect(rotuloInstante('2026-01-01T02:59:00+00:00')).toBe('31/12/2025 23:59')
    expect(rotuloInstante(null)).toBe('—')
  })
})
