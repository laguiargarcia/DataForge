import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import {
  enviarExtrato, etapaDoJob, listarExtratos, rotuloGuardado, rotuloPeriodo, textoEnvio, textoJob,
  type Aceite,
} from './extratos'

type Pedido = { url: string; method: string; tipo: string | undefined; body: unknown }
let pedidos: Pedido[] = []

function responder(status: number, corpo: unknown = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const headers = init.headers as Record<string, string> | undefined
    pedidos.push({ url, method: init.method ?? 'GET', tipo: headers?.['content-type'], body: init.body })
    return { status, ok: status >= 200 && status < 300, json: async () => corpo }
  }))
}

beforeEach(() => { pedidos = [] })
afterEach(() => { vi.unstubAllGlobals() })

const pdf = () => new Blob(['%PDF-1.4 falso'], { type: '' })

const aceite: Aceite = {
  arquivo: 'extrato_2026-09-01_2026-09-30.pdf',
  periodo: { de: '2026-09-01', ate: '2026-09-30' },
  n_lancamentos: 12, n_saldos: 1, linhas_ignoradas: 0, dias_divergentes: [], execution_id: 'exec-1',
}

// --- envio -----------------------------------------------------------------------------------------

test('o PDF vai cru, como application/pdf, ao backend relativo à página', async () => {
  responder(201, aceite)
  const arquivo = pdf()
  await enviarExtrato(arquivo)
  expect(pedidos).toHaveLength(1)
  expect(pedidos[0]).toMatchObject({ url: 'api/extratos', method: 'POST', tipo: 'application/pdf' })
  const corpo = pedidos[0].body as Blob
  expect(corpo).toBeInstanceOf(Blob)
  expect(await corpo.text()).toBe('%PDF-1.4 falso')
})

test('201 vira aceito com o período e as contagens, e o job começa na fila', async () => {
  responder(201, { ...aceite, linhas_ignoradas: 2 })
  const envio = await enviarExtrato(pdf())
  expect(envio).toEqual({
    fase: 'aceito', arquivo: 'extrato_2026-09-01_2026-09-30.pdf', periodo: '01/09/2026 a 30/09/2026',
    contagens: '12 lançamentos · 1 saldo · 2 linhas ignoradas', divergentes: null, execucao: 'exec-1',
    job: { fase: 'rodando', status: 'pending' },
  })
  expect(textoEnvio(envio)).toBe('Extrato aceito: 01/09/2026 a 30/09/2026.')
})

test('dias divergentes viram aviso com os dias, e o extrato segue aceito', async () => {
  responder(201, { ...aceite, dias_divergentes: ['2026-09-03'] })
  expect(await enviarExtrato(pdf())).toMatchObject({
    fase: 'aceito',
    divergentes: 'O saldo de 03/09/2026 difere do de um extrato já guardado. O extrato foi aceito mesmo assim.',
  })
  responder(201, { ...aceite, dias_divergentes: ['2026-09-03', '2026-09-05', '2026-09-10'] })
  expect(await enviarExtrato(pdf())).toMatchObject({
    divergentes: 'Os saldos de 03/09/2026, 05/09/2026 e 10/09/2026 diferem dos de extratos já guardados. ' +
      'O extrato foi aceito mesmo assim.',
  })
})

test.each([422, 409, 413])('%i vira recusa com o detail do backend', async status => {
  responder(status, { detail: 'motivo do backend' })
  const envio = await enviarExtrato(pdf())
  expect(envio).toEqual({ fase: 'recusado', motivo: 'motivo do backend' })
  expect(textoEnvio(envio)).toBe('Extrato recusado: motivo do backend.')
})

test('502 vira guardado sem veredito refeito, sem oferecer tentar de novo', async () => {
  const detail = 'o PDF foi guardado como extrato_2026-09-01_2026-09-30.pdf, mas o job extrato não foi pedido: x'
  responder(502, { detail })
  const envio = await enviarExtrato(pdf())
  expect(envio).toEqual({ fase: 'guardado', motivo: detail })
  expect(textoEnvio(envio)).toBe(`${detail}. O veredito não foi refeito agora: ele sai na próxima rodada diária.`)
})

test('outra falha não é recusa: diz que não foi possível enviar', async () => {
  responder(500)
  const envio = await enviarExtrato(pdf())
  expect(envio).toEqual({ fase: 'erro', motivo: 'Falha ao gravar: HTTP 500' })
  expect(textoEnvio(envio)).toBe('Não foi possível enviar o extrato: Falha ao gravar: HTTP 500.')
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
  expect(await enviarExtrato(pdf())).toEqual({ fase: 'erro', motivo: 'Failed to fetch' })
})

// --- andamento do job extrato ------------------------------------------------------------------------

test('o andamento vai de pendente a concluído', () => {
  const pendente = etapaDoJob({ status: 'pending', reason: null })
  const rodando = etapaDoJob({ status: 'running', reason: null })
  const concluido = etapaDoJob({ status: 'success', reason: null })
  expect([pendente, rodando, concluido]).toEqual([
    { fase: 'rodando', status: 'pending' }, { fase: 'rodando', status: 'running' }, { fase: 'concluido' },
  ])
  expect([pendente, rodando, concluido].map(textoJob)).toEqual([
    'Na fila: esperando outro job do workspace terminar…',
    'Refazendo o veredito…',
    'Veredito refeito: a Conferência já mostra este extrato.',
  ])
})

test('o andamento vai de pendente a falhou, com o motivo quando há', () => {
  const falhou = etapaDoJob({ status: 'failed', reason: 'task extrato falhou' })
  expect(falhou).toEqual({ fase: 'falhou', motivo: 'task extrato falhou' })
  expect(textoJob(falhou)).toBe('O job extrato falhou: task extrato falhou. O PDF continua guardado.')
  expect(textoJob(etapaDoJob({ status: 'failed', reason: null }))).toBe(
    'O job extrato falhou. O PDF continua guardado.')
})

// --- lista dos guardados -----------------------------------------------------------------------------

test('lista os guardados pelo backend', async () => {
  const guardado = { ...aceite, emitido_em: '2026-10-01' }
  responder(200, { arquivos: [guardado] })
  expect(await listarExtratos()).toEqual([guardado])
  expect(pedidos[0]).toMatchObject({ url: 'api/extratos', method: 'GET' })
})

test('período e contagens de um guardado; período nulo quando o arquivo não deixou dia', () => {
  expect(rotuloPeriodo({ de: '2026-08-01', ate: '2026-08-31' })).toBe('01/08/2026 a 31/08/2026')
  expect(rotuloPeriodo({ de: null, ate: null })).toBe('Sem dias nas tabelas')
  expect(rotuloGuardado({
    arquivo: 'a.pdf', periodo: { de: null, ate: null }, emitido_em: null,
    n_lancamentos: 1, n_saldos: 0, linhas_ignoradas: 1,
  })).toEqual({ periodo: 'Sem dias nas tabelas', contagens: '1 lançamento · 0 saldos · 1 linha ignorada' })
})
