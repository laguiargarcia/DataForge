import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  adicionar, anotar, apagar, daParte, desanotar, DOS_DOIS, editar, guardarParte, lerDesejos, lerParteGuardada,
  lerPessoas, parteDe, parteInicial, partes, type Desejo,
} from './api'

const resposta = (status: number, corpo?: unknown) =>
  new Response(corpo === undefined ? null : JSON.stringify(corpo), { status })

function falso(...respostas: Response[]) {
  const f = vi.fn(async (_url: string, _opcoes?: RequestInit) => respostas.shift()!)
  vi.stubGlobal('fetch', f)
  return f
}

const desejo = (titulo: string, de_quem: string | null = null, realizado = false): Desejo => ({
  id: titulo, titulo, de_quem, adicionado_por: 'ana', realizado,
  realizado_em: realizado ? '2026-11-24T11:00:00+00:00' : null, criado_em: '2026-11-24T10:00:00+00:00',
  anotacoes: [{ id: 'n1', texto: 'https://exemplo.invalid/a', link: true }],
})

const PESSOAS = [{ id: 'id-ana', nome: 'ana' }, { id: 'id-beto', nome: 'beto' }]

afterEach(() => vi.unstubAllGlobals())

describe('api dos desejos', () => {
  it('lê os desejos, relativo à página', async () => {
    const f = falso(resposta(200, { desejos: [desejo('bicicleta')] }))
    expect(await lerDesejos()).toEqual([desejo('bicicleta')])
    expect(f.mock.calls[0]).toEqual(['api/desejos', { method: 'GET' }])
  })

  it('lê as pessoas da casa', async () => {
    const f = falso(resposta(200, { pessoas: PESSOAS }))
    expect(await lerPessoas()).toEqual(PESSOAS)
    expect(f.mock.calls[0]).toEqual(['api/pessoas', { method: 'GET' }])
  })

  it('adiciona com título e parte em JSON, sem anotações', async () => {
    const f = falso(resposta(201, desejo('livro')))
    expect(await adicionar('livro', null)).toEqual(desejo('livro'))
    const [url, opcoes] = f.mock.calls[0]
    expect(url).toBe('api/desejos')
    expect(opcoes).toMatchObject({ method: 'POST', body: JSON.stringify({ titulo: 'livro', de_quem: null }) })
    expect(new Headers(opcoes!.headers).get('content-type')).toBe('application/json')
  })

  it('edita só os campos dados, com o id num segmento só', async () => {
    const f = falso(resposta(200, desejo('livro', 'id-beto')))
    expect((await editar('a/..', { de_quem: 'id-beto' })).de_quem).toBe('id-beto')
    const [url, opcoes] = f.mock.calls[0]
    expect(url).toBe('api/desejos/a%2F%2E%2E')
    expect(opcoes).toMatchObject({ method: 'PATCH', body: JSON.stringify({ de_quem: 'id-beto' }) })
  })

  it('apagar responde 204, sem corpo', async () => {
    const f = falso(resposta(204))
    await expect(apagar('d1')).resolves.toBeUndefined()
    expect(f.mock.calls[0]).toEqual(['api/desejos/d1', { method: 'DELETE' }])
  })

  it('anota no fim do desejo e devolve o desejo', async () => {
    const f = falso(resposta(201, desejo('livro')))
    expect(await anotar('d1', 'capa dura')).toEqual(desejo('livro'))
    expect(f.mock.calls[0][0]).toBe('api/desejos/d1/anotacoes')
    expect(f.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ texto: 'capa dura' }) })
  })

  it('apaga uma anotação com os dois ids em segmentos', async () => {
    const f = falso(resposta(204))
    await expect(desanotar('d/1', 'n.1')).resolves.toBeUndefined()
    expect(f.mock.calls[0]).toEqual(['api/desejos/d%2F1/anotacoes/n%2E1', { method: 'DELETE' }])
  })

  it('a recusa traz a mensagem do servidor', async () => {
    falso(resposta(404, { detail: 'desejo não encontrado' }))
    await expect(editar('x', { realizado: true })).rejects.toThrow('desejo não encontrado')
  })

  it('sem mensagem do servidor, diz o status', async () => {
    falso(new Response('<html>', { status: 503 }))
    await expect(lerDesejos()).rejects.toThrow('HTTP 503')
  })
})

describe('partes', () => {
  it('uma por pessoa, na ordem do servidor, e a dos dois no fim', () => {
    expect(partes(PESSOAS)).toEqual([
      { chave: 'id-ana', rotulo: 'ana' }, { chave: 'id-beto', rotulo: 'beto' }, { chave: DOS_DOIS, rotulo: 'Nós dois' }])
  })
  it('sem pessoas, só a dos dois', () => {
    expect(partes([])).toEqual([{ chave: DOS_DOIS, rotulo: 'Nós dois' }])
  })
})

describe('parteDe e daParte', () => {
  it('um desejo sem dono é dos dois', () => {
    expect(parteDe(desejo('a'))).toBe(DOS_DOIS)
    expect(parteDe(desejo('a', 'id-ana'))).toBe('id-ana')
  })
  it('separa os da parte em abertos e realizados, na ordem em que vieram', () => {
    const todos = [desejo('a', 'id-ana'), desejo('b'), desejo('c', 'id-ana', true), desejo('d', 'id-ana'), desejo('e', null, true)]
    const daAna = daParte(todos, 'id-ana')
    expect(daAna.abertos.map(d => d.titulo)).toEqual(['a', 'd'])
    expect(daAna.realizados.map(d => d.titulo)).toEqual(['c'])
    const dosDois = daParte(todos, DOS_DOIS)
    expect(dosDois.abertos.map(d => d.titulo)).toEqual(['b'])
    expect(dosDois.realizados.map(d => d.titulo)).toEqual(['e'])
  })
  it('sem desejos, duas listas vazias', () => {
    expect(daParte([], DOS_DOIS)).toEqual({ abertos: [], realizados: [] })
  })
})

describe('parteInicial', () => {
  const todas = partes(PESSOAS)
  it('a guardada no aparelho, quando ainda existe', () => {
    expect(parteInicial(DOS_DOIS, todas, 'id-ana')).toBe(DOS_DOIS)
    expect(parteInicial('id-beto', todas, 'id-ana')).toBe('id-beto')
  })
  it('sem guardada, ou com uma de quem não está mais na casa, a de quem entrou', () => {
    expect(parteInicial(null, todas, 'id-beto')).toBe('id-beto')
    expect(parteInicial('id-sumiu', todas, 'id-ana')).toBe('id-ana')
  })
  it('quem entrou sem parte própria cai na dos dois', () => {
    expect(parteInicial(null, todas, 'id-outro')).toBe(DOS_DOIS)
  })
})

describe('a parte guardada no aparelho', () => {
  it('guarda e lê de volta', () => {
    const guardado = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => guardado.get(k) ?? null, setItem: (k: string, v: string) => { guardado.set(k, v) },
    })
    expect(lerParteGuardada()).toBeNull()
    guardarParte(DOS_DOIS)
    expect(lerParteGuardada()).toBe(DOS_DOIS)
    guardarParte('id-ana')
    expect(lerParteGuardada()).toBe('id-ana')
  })
  it('sem armazenamento (modo privado, bloqueado) não quebra: lê nada e guardar não faz nada', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('bloqueado') }, setItem: () => { throw new Error('bloqueado') },
    })
    expect(lerParteGuardada()).toBeNull()
    expect(() => guardarParte('id-ana')).not.toThrow()
  })
})
