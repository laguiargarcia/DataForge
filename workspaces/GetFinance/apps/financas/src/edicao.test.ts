import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import {
  aplicar, andamento, corrigir, desfazerCorrecao, ErroEdicao, excluirCategoria, excluirRegra, faseDoAndamento,
  lerEdicao, salvarCategoria, salvarRegra, textoEfeito, textoFaixa,
} from './edicao'

type Pedido = { url: string; method: string; body: unknown }
let pedidos: Pedido[] = []

function responder(status: number, corpo: unknown = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    pedidos.push({ url, method: init.method ?? 'GET', body: init.body ? JSON.parse(init.body as string) : undefined })
    return { status, ok: status >= 200 && status < 300, json: async () => corpo }
  }))
}
const ultimo = () => pedidos[pedidos.length - 1]

beforeEach(() => { pedidos = [] })
afterEach(() => { vi.unstubAllGlobals() })

// --- cliente ---------------------------------------------------------------------------------------

test('pede ao backend relativo à página do app', async () => {
  responder(200, { versao: 3 })
  expect(await lerEdicao()).toEqual({ versao: 3 })
  expect(ultimo()).toEqual({ url: 'api/edicao', method: 'GET', body: undefined })
})

test('criar e alterar categoria', async () => {
  const c = { subcategoria: 'Casa/Reforma', categoria: 'Casa', tipo: 'Essencial' }
  responder(201, c)
  await salvarCategoria(null, c)
  expect(ultimo()).toEqual({ url: 'api/categorias', method: 'POST', body: c })
  await salvarCategoria('Casa/Reforma', c)
  expect(ultimo()).toMatchObject({ url: 'api/categorias/Casa%2FReforma', method: 'PUT' })
})

test('nome com ponto não vira segmento de ponto', async () => {
  responder(204)
  await excluirCategoria('..')
  expect(ultimo().url).toBe('api/categorias/%2E%2E')
  await excluirRegra('LOJA.COM')
  expect(ultimo().url).toBe('api/regras/LOJA%2ECOM')
})

test('regra devolve o efeito', async () => {
  responder(201, { regra: {}, efeito: { casa: 2, vence: 1, barradas: 0 } })
  expect(await salvarRegra(null, { padrao: 'UBER', subcategoria: 'Transporte' })).toEqual({ casa: 2, vence: 1, barradas: 0 })
  expect(ultimo().method).toBe('POST')
  await salvarRegra('UBER', { padrao: 'UBER X', subcategoria: 'Transporte' })
  expect(ultimo()).toMatchObject({ url: 'api/regras/UBER', method: 'PUT' })
})

test('excluir, corrigir e desfazer', async () => {
  responder(204)
  await excluirCategoria('Lazer')
  expect(ultimo()).toMatchObject({ url: 'api/categorias/Lazer', method: 'DELETE' })
  await excluirRegra('PIX* LOJA')
  expect(ultimo().url).toBe('api/regras/PIX*%20LOJA')
  await desfazerCorrecao('t1')
  expect(ultimo()).toMatchObject({ url: 'api/correcoes/t1', method: 'DELETE' })
  responder(200, {})
  await corrigir('t1', 'Contas')
  expect(ultimo()).toEqual({ url: 'api/correcoes/t1', method: 'PUT', body: { subcategoria: 'Contas' } })
})

test('aplicar e andamento', async () => {
  responder(202, { execution_id: 'r1' })
  expect(await aplicar()).toBe('r1')
  expect(ultimo()).toMatchObject({ url: 'api/aplicar', method: 'POST' })
  responder(200, { status: 'running', reason: null })
  expect(await andamento('r1')).toEqual({ status: 'running', reason: null })
  expect(ultimo().url).toBe('api/aplicar/r1')
})

test('a recusa do backend vira ErroEdicao com a mensagem dele', async () => {
  responder(409, { detail: "a subcategoria 'Lazer' já existe" })
  const erro = await salvarCategoria(null, { subcategoria: 'Lazer', categoria: 'L', tipo: 'Essencial' }).catch(e => e)
  expect(erro).toBeInstanceOf(ErroEdicao)
  expect(erro.message).toBe("a subcategoria 'Lazer' já existe")
})

test('detalhe que não é texto (validação do FastAPI) vira mensagem com o status', async () => {
  responder(422, { detail: [{ msg: 'field required' }] })
  await expect(lerEdicao()).rejects.toThrow('Falha ao gravar: HTTP 422')
})

test('resposta de erro sem JSON', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 503, ok: false, json: async () => { throw new SyntaxError('x') } })))
  await expect(aplicar()).rejects.toThrow('HTTP 503')
})

// --- textos ----------------------------------------------------------------------------------------

test('efeito sem barradas', () => {
  expect(textoEfeito({ casa: 3, vence: 2, barradas: 0 }, 'Groceries')).toEqual({ resumo: 'Casa 3 transações; vale em 2.', aviso: null })
  expect(textoEfeito({ casa: 1, vence: 1, barradas: 0 }, 'Groceries').resumo).toBe('Casa 1 transação; vale em 1.')
  expect(textoEfeito({ casa: 0, vence: 0, barradas: 0 }, 'Groceries').resumo).toBe('Nenhuma transação tem esse padrão por enquanto.')
})

test('efeito com barradas avisa a direção', () => {
  const { resumo, aviso } = textoEfeito({ casa: 3, vence: 3, barradas: 2 }, 'Groceries')
  expect(resumo).toBe('Casa 3 transações; vale em 1.')
  expect(aviso).toBe('2 delas são do lado oposto ao Tipo de «Groceries» (entrada com Tipo de saída, ou o contrário): ' +
    'a regra não vale nelas, e elas continuam na categoria de antes.')
  expect(textoEfeito({ casa: 1, vence: 1, barradas: 1 }, 'X').aviso).toMatch(/^1 delas é do lado oposto/)
})

test('texto da faixa em cada fase', () => {
  expect(textoFaixa({ fase: 'em_dia' })).toBeNull()
  expect(textoFaixa({ fase: 'pendente' })).toBe('Há edições ainda não recalculadas.')
  expect(textoFaixa({ fase: 'rodando', status: 'running' })).toBe('Recalculando…')
  expect(textoFaixa({ fase: 'rodando', status: 'pending' })).toBe('Na fila: esperando outro job do workspace terminar…')
  expect(textoFaixa({ fase: 'falhou', motivo: 'classify falhou' })).toBe('O recálculo falhou: classify falhou. As edições continuam salvas.')
  expect(textoFaixa({ fase: 'falhou', motivo: null })).toBe('O recálculo falhou. As edições continuam salvas.')
})

test('fase da faixa pelo andamento', () => {
  expect(faseDoAndamento({ status: 'success', reason: null })).toBe('terminou')
  expect(faseDoAndamento({ status: 'pending', reason: null })).toEqual({ fase: 'rodando', status: 'pending' })
  expect(faseDoAndamento({ status: 'running', reason: null })).toEqual({ fase: 'rodando', status: 'running' })
  expect(faseDoAndamento({ status: 'failed', reason: 'erro' })).toEqual({ fase: 'falhou', motivo: 'erro' })
})
