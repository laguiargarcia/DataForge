import { afterEach, describe, expect, it, vi } from 'vitest'
import { entrar, lerEu, lerEuOuNinguem, permitidos, sair, telaAberta } from './eu'
import { aoFicarSemSessao } from './pedido'

const CASCA = [{ id: 'financas' }, { id: 'lista' }]

afterEach(() => vi.unstubAllGlobals())

describe('permitidos', () => {
  it('fica só com os módulos da pessoa, na ordem da casca', () => {
    expect(permitidos(CASCA, { id: 'id-ana', nome: 'ana', modulos: ['lista', 'financas'] })).toEqual(CASCA)
    expect(permitidos(CASCA, { id: 'id-beto', nome: 'beto', modulos: ['lista'] })).toEqual([{ id: 'lista' }])
  })
  it('ignora um módulo que a casca não conhece e aceita pessoa sem módulo', () => {
    expect(permitidos(CASCA, { id: 'id-ana', nome: 'ana', modulos: ['agenda'] })).toEqual([])
    expect(permitidos(CASCA, { id: 'id-ana', nome: 'ana', modulos: [] })).toEqual([])
  })
})

describe('telaAberta', () => {
  it('abre a pedida quando a pessoa a tem', () => {
    expect(telaAberta(CASCA, 'lista')).toBe('lista')
  })
  it('cai na primeira que a pessoa tem quando a pedida não é dela ou não existe', () => {
    expect(telaAberta([{ id: 'lista' }], 'financas')).toBe('lista')
    expect(telaAberta(CASCA, null)).toBe('financas')
    expect(telaAberta(CASCA, 'outra')).toBe('financas')
  })
  it('sem nenhum módulo não há tela', () => {
    expect(telaAberta([], 'lista')).toBeNull()
  })
})

describe('lerEu', () => {
  it('pede api/eu e devolve id, nome e módulos', async () => {
    const fetchFalso = vi.fn(async () => new Response(JSON.stringify({ id: 'id-ana', nome: 'ana', modulos: ['lista'] })))
    vi.stubGlobal('fetch', fetchFalso)
    expect(await lerEu()).toEqual({ id: 'id-ana', nome: 'ana', modulos: ['lista'] })
    expect(fetchFalso).toHaveBeenCalledWith('api/eu', { method: 'GET' })
  })
  it('falha com o status quando o servidor recusa', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })))
    await expect(lerEu()).rejects.toThrow('HTTP 503')
  })
})

describe('lerEuOuNinguem', () => {
  it('devolve quem entrou', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ nome: 'ana', modulos: [] }))))
    expect(await lerEuOuNinguem()).toEqual({ nome: 'ana', modulos: [] })
  })
  it('sem sessão (401) não é erro: é ninguém', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'sem sessão' }), { status: 401 })))
    expect(await lerEuOuNinguem()).toBeNull()
  })
  it('qualquer outra recusa segue sendo erro', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })))
    await expect(lerEuOuNinguem()).rejects.toThrow('HTTP 503')
  })
})

describe('entrar', () => {
  it('manda nome e senha em JSON a api/entrar e diz que entrou no 204', async () => {
    const fetchFalso = vi.fn(async (_url: string, _opcoes?: RequestInit) => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchFalso)
    expect(await entrar('ana', 'senha-falsa')).toBe(true)
    const [url, opcoes] = fetchFalso.mock.calls[0]
    expect([url, opcoes!.method, opcoes!.body]).toEqual(['api/entrar', 'POST', JSON.stringify({ nome: 'ana', senha: 'senha-falsa' })])
    expect(new Headers(opcoes!.headers).get('content-type')).toBe('application/json')
  })
  it.each([401, 422])('um %i é nome ou senha errados, e não avisa que a sessão caiu', async status => {
    const aviso = vi.fn()
    const parar = aoFicarSemSessao(aviso)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status })))
    expect(await entrar('ana', 'errada')).toBe(false)
    expect(aviso).not.toHaveBeenCalled()
    parar()
  })
  it('servidor fora do ar é erro, não senha errada', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 503 })))
    await expect(entrar('ana', 'senha-falsa')).rejects.toThrow('HTTP 503')
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('sem rede') }))
    await expect(entrar('ana', 'senha-falsa')).rejects.toThrow('sem rede')
  })
})

describe('sair', () => {
  it('pede POST api/sair', async () => {
    const fetchFalso = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchFalso)
    await sair()
    expect(fetchFalso).toHaveBeenCalledWith('api/sair', { method: 'POST' })
  })
})
