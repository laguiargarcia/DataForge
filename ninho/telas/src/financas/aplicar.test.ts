import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErroPedido } from '../pedido'
import { type Execucao } from './api'
import { Aplicador, ESPERA, guardaLocal, PAUSA, textoFim, textoSemContato, textoAndamento, type Guarda, type Motor } from './aplicar'

const FIM = '2026-11-24T10:00:00'
const exec = (state: string, fim: string | null = null, extra: Partial<Execucao> = {}): Execucao =>
  ({ run_id: 'r1', state, finished_at: fim, ...extra })

function guardaFalsa(inicial: string | null = null): Guarda & { id: string | null } {
  const g = {
    id: inicial,
    ler: () => g.id,
    guardar: (id: string) => { g.id = id },
    esquecer: () => { g.id = null },
  }
  return g
}

/** Um pedido que só responde quando o teste manda: o primeiro aplicar fica vivo enquanto o teste age. */
function adiado<T>() {
  let responder!: (v: T) => void
  const promessa = new Promise<T>(r => { responder = r })
  return { promessa, responder }
}

function montar(motor: Motor, guarda = guardaFalsa()) {
  const vistos: string[] = []
  const semContato: unknown[] = []
  const dormir = vi.fn(async (_ms: number) => {})
  const aplicador = new Aplicador(motor, guarda, {
    aoMudar: e => vistos.push(e.state),
    aoPerderContato: erro => semContato.push(erro),
  }, dormir)
  return { aplicador, guarda, vistos, semContato, dormir }
}

describe('Aplicador.iniciar', () => {
  it('pede, guarda o id, segue com espera até a hora do fim, avisa cada estado e esquece o id', async () => {
    const estados = [exec('running'), exec('success', FIM)]
    const motor = { pedirAplicar: vi.fn(async () => exec('pending')), andamento: vi.fn(async () => estados.shift()!) }
    const guarda = guardaFalsa()
    const guardou = vi.spyOn(guarda, 'guardar')
    const { aplicador, vistos } = montar(motor, guarda)
    const fim = await aplicador.iniciar()
    expect(fim).toEqual({ tipo: 'acabou', execucao: exec('success', FIM) })
    expect(vistos).toEqual(['pending', 'running', 'success'])
    expect(guardou).toHaveBeenCalledWith('r1')
    expect(motor.andamento).toHaveBeenCalledTimes(2)
    expect(motor.andamento).toHaveBeenCalledWith('r1', ESPERA)
    expect(guarda.id).toBeNull()
  })

  it('para numa falha, que também tem hora do fim', async () => {
    const motor = {
      pedirAplicar: async () => exec('pending'),
      andamento: async () => exec('failed', FIM, { failed_task: 'classify', reason: 'x' }),
    }
    const fim = await montar(motor).aplicador.iniciar()
    expect(fim?.tipo === 'acabou' && fim.execucao.state).toBe('failed')
  })

  it('não segue uma execução que já acabou na resposta do pedido, nem guarda o id', async () => {
    const andamento = vi.fn()
    const { aplicador, guarda } = montar({ pedirAplicar: async () => exec('success', FIM), andamento })
    await aplicador.iniciar()
    expect(andamento).not.toHaveBeenCalled()
    expect(guarda.id).toBeNull()
  })

  it('um erro do próprio pedido diz que não começou, e libera um novo pedido', async () => {
    const motor = { pedirAplicar: vi.fn(async () => { throw new Error('502') }), andamento: vi.fn() }
    const { aplicador, guarda } = montar(motor)
    const fim = await aplicador.iniciar()
    expect(fim?.tipo).toBe('naoComecou')
    expect(textoFim(fim!)).toBe('O aplicar não começou: 502')
    expect(guarda.id).toBeNull()
    await aplicador.iniciar()
    expect(motor.pedirAplicar).toHaveBeenCalledTimes(2)
  })

  // Caso 1: toque duplo em "Aplicar" ou em "Tentar de novo".
  it('um segundo toque antes de o pedido responder não pede outra execução', async () => {
    const pedido = adiado<Execucao>()
    const motor = { pedirAplicar: vi.fn(() => pedido.promessa), andamento: vi.fn(async () => exec('success', FIM)) }
    const { aplicador } = montar(motor)
    const primeiro = aplicador.iniciar()
    expect(aplicador.iniciar()).toBeNull()
    expect(aplicador.retomar()).toBeNull()
    pedido.responder(exec('pending'))
    await primeiro
    expect(motor.pedirAplicar).toHaveBeenCalledTimes(1)
  })

  it('enquanto segue a execução, outro toque também não pede outra', async () => {
    const consulta = adiado<Execucao>()
    const motor = { pedirAplicar: vi.fn(async () => exec('pending')), andamento: vi.fn(() => consulta.promessa) }
    const { aplicador } = montar(motor)
    const primeiro = aplicador.iniciar()
    await vi.waitFor(() => expect(motor.andamento).toHaveBeenCalled())
    expect(aplicador.iniciar()).toBeNull()
    consulta.responder(exec('success', FIM))
    await primeiro
    expect(motor.pedirAplicar).toHaveBeenCalledTimes(1)
  })

  // Caso 2: um erro ao acompanhar (rede caiu, 502, aba suspensa pelo Safari).
  it('um erro ao acompanhar avisa que o aplicar segue e volta a acompanhar o mesmo id, sem novo pedido', async () => {
    const respostas: (() => Execucao)[] = [
      () => { throw new TypeError('Load failed') },
      () => { throw new ErroPedido('o DataForge não respondeu', 502) },
      () => exec('success', FIM),
    ]
    const motor = { pedirAplicar: vi.fn(async () => exec('pending')), andamento: vi.fn(async (_id: string, _esperar: number) => respostas.shift()!()) }
    const { aplicador, guarda, semContato, dormir } = montar(motor)
    const fim = await aplicador.iniciar()
    expect(fim?.tipo).toBe('acabou')
    expect(motor.pedirAplicar).toHaveBeenCalledTimes(1)
    expect(motor.andamento.mock.calls.map(c => c[0])).toEqual(['r1', 'r1', 'r1'])
    expect(semContato.map(e => textoSemContato(e))).toEqual([
      'O aplicar segue, mas o servidor não respondeu (Load failed). Tentando acompanhar de novo…',
      'O aplicar segue, mas o servidor não respondeu (o DataForge não respondeu). Tentando acompanhar de novo…',
    ])
    expect(dormir).toHaveBeenCalledWith(PAUSA)
    expect(guarda.id).toBeNull()
  })

  it('enquanto não há contato, guarda o id e um toque não pede outra execução', async () => {
    const pausa = adiado<void>()
    const motor = {
      pedirAplicar: vi.fn(async () => exec('pending')),
      andamento: vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(exec('success', FIM)),
    }
    const guarda = guardaFalsa()
    const semContato: unknown[] = []
    const aplicador = new Aplicador(motor, guarda, { aoMudar: () => {}, aoPerderContato: e => semContato.push(e) },
      () => pausa.promessa)
    const primeiro = aplicador.iniciar()
    await vi.waitFor(() => expect(semContato).toHaveLength(1))
    expect(guarda.id).toBe('r1')
    expect(aplicador.iniciar()).toBeNull()
    pausa.responder()
    await primeiro
    expect(motor.pedirAplicar).toHaveBeenCalledTimes(1)
  })
})

// Caso 3: recarregar a página (ou o Safari reabrir a aba) durante um aplicar.
describe('Aplicador.retomar', () => {
  it('com um id guardado, acompanha esse id e não pede outra execução', async () => {
    const estados = [exec('running'), exec('success', FIM)]
    const motor = { pedirAplicar: vi.fn(), andamento: vi.fn(async () => estados.shift()!) }
    const { aplicador, guarda, vistos } = montar(motor, guardaFalsa('r1'))
    const fim = aplicador.retomar()
    expect(aplicador.iniciar()).toBeNull()
    expect(await fim).toEqual({ tipo: 'acabou', execucao: exec('success', FIM) })
    expect(motor.pedirAplicar).not.toHaveBeenCalled()
    expect(motor.andamento.mock.calls).toEqual([['r1', 0], ['r1', ESPERA]])
    expect(vistos).toEqual(['running', 'success'])
    expect(guarda.id).toBeNull()
  })

  it('sem id guardado, não faz nada', () => {
    const motor = { pedirAplicar: vi.fn(), andamento: vi.fn() }
    expect(montar(motor).aplicador.retomar()).toBeNull()
    expect(motor.andamento).not.toHaveBeenCalled()
  })

  it.each([404, 422])('um id que o servidor não conhece (%i) é esquecido e a tela volta ao normal', async status => {
    const motor = { pedirAplicar: vi.fn(), andamento: vi.fn(async () => { throw new ErroPedido('x', status) }) }
    const { aplicador, guarda, semContato } = montar(motor, guardaFalsa('r1'))
    const fim = await aplicador.retomar()
    expect(fim).toEqual({ tipo: 'esquecida' })
    expect(textoFim(fim!)).toBeNull()
    expect(guarda.id).toBeNull()
    expect(semContato).toEqual([])
    expect(motor.pedirAplicar).not.toHaveBeenCalled()
  })
})

describe('guardaLocal', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('guarda, lê e esquece o id no armazenamento do aparelho', () => {
    const itens = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => itens.get(k) ?? null,
      setItem: (k: string, v: string) => { itens.set(k, v) },
      removeItem: (k: string) => { itens.delete(k) },
    })
    const g = guardaLocal('chave')
    expect(g.ler()).toBeNull()
    g.guardar('r1')
    expect([g.ler(), itens.get('chave')]).toEqual(['r1', 'r1'])
    g.esquecer()
    expect(g.ler()).toBeNull()
  })

  it('sem armazenamento (aba privada, bloqueado), não quebra: só não lembra', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('SecurityError') },
      setItem: () => { throw new Error('QuotaExceeded') },
      removeItem: () => { throw new Error('SecurityError') },
    })
    const g = guardaLocal('chave')
    expect(() => { g.guardar('r1'); g.esquecer() }).not.toThrow()
    expect(g.ler()).toBeNull()
  })
})

describe('textos', () => {
  it('textoAndamento diz a tarefa e o motivo da falha', () => {
    expect(textoAndamento(exec('failed', 'x', { failed_task: 'classify', reason: 'saldo' })))
      .toBe('O aplicar falhou na tarefa classify: saldo')
    expect(textoAndamento(exec('failed', 'x'))).toBe('O aplicar falhou.')
  })

  it('textoAndamento diz se está na fila ou rodando', () => {
    expect(textoAndamento(exec('pending'))).toMatch(/fila/)
    expect(textoAndamento(exec('running'))).toMatch(/recalculando/)
    expect(textoAndamento(exec('success', 'x'))).toBe('Aplicado.')
  })

  it('textoFim só tem texto para falha ou para o pedido que não começou', () => {
    expect(textoFim({ tipo: 'acabou', execucao: exec('success', FIM) })).toBeNull()
    expect(textoFim({ tipo: 'acabou', execucao: exec('failed', FIM) })).toBe('O aplicar falhou.')
    expect(textoFim({ tipo: 'naoComecou', erro: 'sem rede' })).toBe('O aplicar não começou: sem rede')
  })
})
