import { afterEach, describe, expect, it, vi } from 'vitest'
import { aoFicarSemSessao, ErroPedido, pedir, segmento, sessaoMudou } from './pedido'

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

  it('um 401 avisa quem ouve que não há sessão, e ainda recusa o pedido', async () => {
    const aviso = vi.fn()
    const parar = aoFicarSemSessao(aviso)
    falso(new Response(JSON.stringify({ detail: 'sem sessão' }), { status: 401 }))
    await expect(pedir('GET', 'api/x')).rejects.toMatchObject({ message: 'sem sessão', status: 401 })
    expect(aviso).toHaveBeenCalledTimes(1)
    parar()
    falso(new Response('{}', { status: 401 }))
    await expect(pedir('GET', 'api/x')).rejects.toThrow('HTTP 401')
    expect(aviso).toHaveBeenCalledTimes(1)
  })

  it('as outras recusas e as respostas boas não avisam', async () => {
    const aviso = vi.fn()
    const parar = aoFicarSemSessao(aviso)
    for (const status of [403, 404, 422, 503]) {
      falso(new Response('{}', { status }))
      await expect(pedir('GET', 'api/x')).rejects.toBeInstanceOf(ErroPedido)
    }
    falso(new Response('{}', { status: 200 }))
    await pedir('GET', 'api/x')
    expect(aviso).not.toHaveBeenCalled()
    parar()
  })

  it('parar de ouvir não tira quem passou a ouvir depois', async () => {
    const [velho, novo] = [vi.fn(), vi.fn()]
    const pararVelho = aoFicarSemSessao(velho)
    aoFicarSemSessao(novo)
    pararVelho()
    falso(new Response('{}', { status: 401 }))
    await expect(pedir('GET', 'api/x')).rejects.toBeInstanceOf(ErroPedido)
    expect([velho.mock.calls.length, novo.mock.calls.length]).toEqual([0, 1])
  })

  it('o 401 de um pedido feito antes de a sessão mudar não avisa: é da sessão velha', async () => {
    const aviso = vi.fn()
    const parar = aoFicarSemSessao(aviso)
    let responder!: (r: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(r => { responder = r })))
    const velho = pedir('GET', 'api/x')
    sessaoMudou()   // alguém entrou enquanto o pedido estava em voo
    responder(new Response('{}', { status: 401 }))
    await expect(velho).rejects.toMatchObject({ status: 401 })
    expect(aviso).not.toHaveBeenCalled()
    falso(new Response('{}', { status: 401 }))
    await expect(pedir('GET', 'api/x')).rejects.toBeInstanceOf(ErroPedido)   // um pedido da sessão nova avisa
    expect(aviso).toHaveBeenCalledTimes(1)
    parar()
  })

  it('segmento troca "/" e "."', () => {
    expect(segmento('../x')).toBe('%2E%2E%2Fx')
  })
})
