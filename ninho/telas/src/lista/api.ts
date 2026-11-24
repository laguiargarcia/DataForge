// Cliente das rotas da lista de compras (ninho/lista/rotas.py). Como em finanças, a página vem do
// mesmo servidor atrás do login da pessoa (HTTP Basic), que o navegador manda em cada pedido daqui.

import { pedir as pedirAoServidor, segmento } from '../pedido'

/** Um item da lista, como ninho/lista/itens.py o devolve (horas em ISO, UTC). */
export type Item = {
  id: string
  texto: string
  comprado: boolean
  adicionado_por: string
  criado_em: string
  comprado_em: string | null
}

/** O tamanho máximo do texto de um item (LIMITE em ninho/lista/itens.py). */
export const LIMITE = 200

const pedir = <T>(metodo: string, caminho: string, corpo?: unknown) =>
  pedirAoServidor<T>(metodo, `api/lista/${caminho}`, corpo)

/** Todos os itens: os que faltam primeiro (mais novos em cima), depois os comprados. */
export const lerItens = () => pedir<{ itens: Item[] }>('GET', 'itens').then(c => c.itens)
export const adicionar = (texto: string) => pedir<Item>('POST', 'itens', { texto })
export const marcar = (id: string, comprado: boolean) => pedir<Item>('PATCH', `itens/${segmento(id)}`, { comprado })
export const apagar = (id: string) => pedir<void>('DELETE', `itens/${segmento(id)}`)
export const limparComprados = () =>
  pedir<{ apagados: number }>('POST', 'limpar-comprados').then(c => c.apagados)

/** Os itens em duas partes, na ordem em que vieram: os que faltam comprar e os comprados. */
export function separar(itens: readonly Item[]): { faltam: Item[]; comprados: Item[] } {
  return { faltam: itens.filter(i => !i.comprado), comprados: itens.filter(i => i.comprado) }
}
