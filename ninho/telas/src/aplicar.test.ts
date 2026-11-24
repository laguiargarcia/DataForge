import { describe, expect, it, vi } from 'vitest'
import type { Execucao } from './api'
import { aplicar, ESPERA, textoAndamento } from './aplicar'

const exec = (state: string, fim: string | null = null, extra: Partial<Execucao> = {}): Execucao =>
  ({ run_id: 'r1', state, finished_at: fim, ...extra })

describe('aplicar', () => {
  it('pede, segue com espera até a hora do fim e avisa cada estado', async () => {
    const estados = [exec('running'), exec('success', '2026-11-24T10:00:00')]
    const motor = { pedirAplicar: vi.fn(async () => exec('pending')), andamento: vi.fn(async () => estados.shift()!) }
    const vistos: string[] = []
    const fim = await aplicar(motor, e => vistos.push(e.state))
    expect(fim.state).toBe('success')
    expect(vistos).toEqual(['pending', 'running', 'success'])
    expect(motor.andamento).toHaveBeenCalledTimes(2)
    expect(motor.andamento).toHaveBeenCalledWith('r1', ESPERA)
  })

  it('para numa falha, que também tem hora do fim', async () => {
    const motor = {
      pedirAplicar: async () => exec('pending'),
      andamento: async () => exec('failed', '2026-11-24T10:00:00', { failed_task: 'classify', reason: 'x' }),
    }
    expect((await aplicar(motor, () => {})).state).toBe('failed')
  })

  it('não segue uma execução que já acabou na resposta do pedido', async () => {
    const andamento = vi.fn()
    await aplicar({ pedirAplicar: async () => exec('success', '2026-11-24T10:00:00'), andamento }, () => {})
    expect(andamento).not.toHaveBeenCalled()
  })

  it('um erro do pedido chega a quem chamou', async () => {
    const motor = { pedirAplicar: async () => { throw new Error('502') }, andamento: vi.fn() }
    await expect(aplicar(motor, () => {})).rejects.toThrow('502')
  })
})

describe('textoAndamento', () => {
  it('diz a tarefa e o motivo da falha', () => {
    expect(textoAndamento(exec('failed', 'x', { failed_task: 'classify', reason: 'saldo' })))
      .toBe('O aplicar falhou na tarefa classify: saldo')
    expect(textoAndamento(exec('failed', 'x'))).toBe('O aplicar falhou.')
  })

  it('diz se está na fila ou rodando', () => {
    expect(textoAndamento(exec('pending'))).toMatch(/fila/)
    expect(textoAndamento(exec('running'))).toMatch(/recalculando/)
    expect(textoAndamento(exec('success', 'x'))).toBe('Aplicado.')
  })
})
