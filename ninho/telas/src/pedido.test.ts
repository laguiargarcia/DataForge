import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErroPedido, pedir, segmento } from './pedido'

function falso(resposta: Response) {
  const f = vi.fn(async (_url: string, _opcoes?: RequestInit) => resposta)
  vi.stubGlobal('fetch', f)
  return f
}

afterEach(() => vi.unstubAllGlobals())

describe('pedido', () => {
  it('sem corpo, manda só o método ao endereço dado', async () => {
    const f = falso(new Response(JSON.stringify({ a: 1 }), { status: 200 }))
    expect(await pedir('GET', 'api/x')).toEqual({ a: 1 })
    expect(f.mock.calls[0]).toEqual(['api/x', { method: 'GET' }])
  })

  it('com corpo, manda JSON', async () => {
    const f = falso(new Response(JSON.stringify({}), { status: 201 }))
    await pedir('POST', 'api/x', { texto: 'pão' })
    const opcoes = f.mock.calls[0][1]!
    expect(opcoes).toMatchObject({ method: 'POST', body: JSON.stringify({ texto: 'pão' }) })
    expect(new Headers(opcoes.headers).get('content-type')).toBe('application/json')
  })

  it('um 204 não tem corpo', async () => {
    falso(new Response(null, { status: 204 }))
    await expect(pedir('DELETE', 'api/x')).resolves.toBeUndefined()
  })

  it('a recusa traz a mensagem do servidor e o status', async () => {
    falso(new Response(JSON.stringify({ detail: 'não pode' }), { status: 422 }))
    const erro = await pedir('POST', 'api/x', {}).catch((e: ErroPedido) => e) as ErroPedido
    expect(erro).toBeInstanceOf(ErroPedido)
    expect([erro.message, erro.status]).toEqual(['não pode', 422])
  })

  it('sem mensagem do servidor, ou com uma que não é texto, diz o status', async () => {
    falso(new Response('<html>', { status: 502 }))
    await expect(pedir('GET', 'api/x')).rejects.toThrow('Falha no pedido: HTTP 502')
    falso(new Response(JSON.stringify({ detail: [{ msg: 'x' }] }), { status: 422 }))
    await expect(pedir('GET', 'api/x')).rejects.toThrow('Falha no pedido: HTTP 422')
  })

  it('segmento troca "/" e "."', () => {
    expect(segmento('../x')).toBe('%2E%2E%2Fx')
  })
})
