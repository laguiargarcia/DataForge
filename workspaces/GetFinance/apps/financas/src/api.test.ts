import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { lerTabela } from './api'

let urls: string[] = []

function mockFetch(status: number, body: unknown = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url)
    return { status, ok: status >= 200 && status < 300, json: async () => body }
  }))
}
function mockFetchReject() {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
}
function setLocation(pathname: string) { vi.stubGlobal('location', { pathname }) }
const lastUrl = () => urls[urls.length - 1]

beforeEach(() => { urls = []; setLocation('/apps/GetFinance-Show/financas/') })
afterEach(() => { vi.unstubAllGlobals() })

test('404 vira null', async () => { mockFetch(404); expect(await lerTabela('x')).toBeNull() })
test('500 lança erro', async () => { mockFetch(500); await expect(lerTabela('x')).rejects.toThrow() })
test('falha de rede lança erro', async () => { mockFetchReject(); await expect(lerTabela('x')).rejects.toThrow() })
test('resposta cortada lança erro', async () => {
  mockFetch(200, { columns: [], rows: [], truncated: true })
  await expect(lerTabela('x')).rejects.toThrow()
})
test('usa o workspace da URL', async () => {
  setLocation('/apps/Outro-WS/financas/'); mockFetch(200, { columns: [], rows: [], truncated: false })
  await lerTabela('t'); expect(lastUrl()).toContain('/api/workspaces/Outro-WS/tables/t')
})
test('decodifica o workspace e devolve as linhas', async () => {
  setLocation('/apps/Meu%20WS/financas/'); mockFetch(200, { columns: ['a'], rows: [{ a: 1 }], truncated: false })
  expect(await lerTabela('t')).toEqual([{ a: 1 }])
  expect(lastUrl()).toContain('/api/workspaces/Meu%20WS/tables/t')
})
test('sem segmento apps (dev) usa /api e o workspace padrão', async () => {
  setLocation('/'); mockFetch(200, { columns: [], rows: [], truncated: false })
  await lerTabela('t'); expect(lastUrl()).toBe('/api/workspaces/GetFinance-Show/tables/t')
})
