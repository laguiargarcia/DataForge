import { afterEach, describe, expect, it, vi } from 'vitest'
import { adicionar, apagar, lerItens, limparComprados, marcar, separar, type Item } from './api'

const resposta = (status: number, corpo?: unknown) =>
  new Response(corpo === undefined ? null : JSON.stringify(corpo), { status })

function falso(...respostas: Response[]) {
  const f = vi.fn(async (_url: string, _opcoes?: RequestInit) => respostas.shift()!)
  vi.stubGlobal('fetch', f)
  return f
}

const item = (texto: string, comprado = false): Item => ({
  id: texto, texto, comprado, adicionado_por: '', criado_em: '2026-11-24T10:00:00+00:00',
  comprado_em: comprado ? '2026-11-24T11:00:00+00:00' : null,
})

afterEach(() => vi.unstubAllGlobals())

describe('api da lista', () => {
  it('lê os itens, relativo à página', async () => {
    const f = falso(resposta(200, { itens: [item('pão')] }))
    expect(await lerItens()).toEqual([item('pão')])
    expect(f.mock.calls[0][0]).toBe('api/lista/itens')
    expect(f.mock.calls[0][1]).toEqual({ method: 'GET' })
  })

  it('adiciona com o texto em JSON', async () => {
    const f = falso(resposta(201, item('leite')))
    expect(await adicionar('leite')).toEqual(item('leite'))
    const [url, opcoes] = f.mock.calls[0]
    expect(url).toBe('api/lista/itens')
    expect(opcoes).toMatchObject({ method: 'POST', body: JSON.stringify({ texto: 'leite' }) })
    expect(new Headers(opcoes!.headers).get('content-type')).toBe('application/json')
  })

  it('marca com o id num segmento só', async () => {
    const f = falso(resposta(200, item('ovos', true)))
    expect((await marcar('a/..', true)).comprado).toBe(true)
    const [url, opcoes] = f.mock.calls[0]
    expect(url).toBe('api/lista/itens/a%2F%2E%2E')
    expect(opcoes).toMatchObject({ method: 'PATCH', body: JSON.stringify({ comprado: true }) })
  })

  it('apagar responde 204, sem corpo', async () => {
    const f = falso(resposta(204))
    await expect(apagar('x1')).resolves.toBeUndefined()
    expect(f.mock.calls[0]).toEqual(['api/lista/itens/x1', { method: 'DELETE' }])
  })

  it('limpar os comprados diz quantos saíram', async () => {
    const f = falso(resposta(200, { apagados: 2 }))
    expect(await limparComprados()).toBe(2)
    expect(f.mock.calls[0]).toEqual(['api/lista/limpar-comprados', { method: 'POST' }])
  })

  it('a recusa traz a mensagem do servidor', async () => {
    falso(resposta(404, { detail: 'item não encontrado' }))
    await expect(marcar('x', false)).rejects.toThrow('item não encontrado')
  })

  it('sem mensagem do servidor, diz o status', async () => {
    falso(new Response('<html>', { status: 503 }))
    await expect(lerItens()).rejects.toThrow('HTTP 503')
  })

  it('uma mensagem que não é texto também vira o status', async () => {
    falso(resposta(422, { detail: [{ msg: 'campo faltando' }] }))
    await expect(adicionar('')).rejects.toThrow('HTTP 422')
  })
})

describe('separar', () => {
  it('põe os que faltam e os comprados à parte, mantendo a ordem', () => {
    const itens = [item('a'), item('b', true), item('c'), item('d', true)]
    const { faltam, comprados } = separar(itens)
    expect(faltam.map(i => i.texto)).toEqual(['a', 'c'])
    expect(comprados.map(i => i.texto)).toEqual(['b', 'd'])
  })

  it('lista vazia, duas partes vazias', () => {
    expect(separar([])).toEqual({ faltam: [], comprados: [] })
  })
})
