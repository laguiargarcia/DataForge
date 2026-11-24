import { afterEach, describe, expect, it, vi } from 'vitest'
import { andamento, corrigir, desfazer, ErroPedido, lerTransacoes, segmento } from './api'

const resposta = (status: number, corpo?: unknown) =>
  new Response(corpo === undefined ? null : JSON.stringify(corpo), { status })

function falso(...respostas: Response[]) {
  const f = vi.fn(async (_url: string, _opcoes?: RequestInit) => respostas.shift()!)
  vi.stubGlobal('fetch', f)
  return f
}

afterEach(() => vi.unstubAllGlobals())

describe('api', () => {
  it('pede as transações do mês, relativo à página', async () => {
    const f = falso(resposta(200, { mes: '2026-06', transacoes: [{ id: 'a' }] }))
    expect(await lerTransacoes('2026-06')).toEqual([{ id: 'a' }])
    expect(f.mock.calls[0][0]).toBe('api/financas/transacoes?mes=2026-06')
  })

  it('grava a correção em JSON, com o id num segmento só', async () => {
    const f = falso(resposta(200, { transacao_id: 'a/..', subcategoria: 'Mercado' }))
    await corrigir('a/..', 'Mercado')
    const [url, opcoes] = f.mock.calls[0]
    expect(url).toBe('api/financas/correcoes/a%2F%2E%2E')
    expect(opcoes).toMatchObject({ method: 'PUT', body: JSON.stringify({ subcategoria: 'Mercado' }) })
  })

  it('um 204 não tem corpo', async () => {
    falso(resposta(204))
    await expect(desfazer('a')).resolves.toBeUndefined()
  })

  it('a recusa traz a mensagem do servidor e o status', async () => {
    falso(resposta(422, { detail: 'categoria desconhecida' }))
    const erro = await corrigir('a', 'x').catch(e => e)
    expect(erro).toBeInstanceOf(ErroPedido)
    expect([erro.message, erro.status]).toEqual(['categoria desconhecida', 422])
  })

  it('sem mensagem do servidor, diz o status', async () => {
    falso(new Response('<html>', { status: 502 }))
    await expect(andamento('r1', 20)).rejects.toThrow('HTTP 502')
  })

  it('o andamento pede a espera', async () => {
    const f = falso(resposta(200, { run_id: 'r1', state: 'running' }))
    await andamento('r1', 20)
    expect(f.mock.calls[0][0]).toBe('api/financas/aplicar/r1?esperar=20')
  })

  it('segmento troca "/" e "."', () => {
    expect(segmento('../x')).toBe('%2E%2E%2Fx')
  })
})
