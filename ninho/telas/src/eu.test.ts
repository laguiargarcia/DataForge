import { afterEach, describe, expect, it, vi } from 'vitest'
import { lerEu, permitidos, telaAberta } from './eu'

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
