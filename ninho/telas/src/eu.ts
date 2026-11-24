// Quem entrou e quais módulos pode abrir (GET /api/eu, ninho/servidor/app.py). A casca lê isto antes
// de montar as telas: a barra mostra só os módulos da pessoa (docs/architecture-v2/people-and-visibility.md).

import { pedir } from './pedido'

/** `id` é o da pessoa em /api/pessoas: a tela dos desejos abre na parte dela. */
export type Eu = { id: string; nome: string; modulos: string[] }

export const lerEu = () => pedir<Eu>('GET', 'api/eu')

/** Os módulos da casca que a pessoa tem, na ordem da casca. */
export function permitidos<T extends { id: string }>(todos: readonly T[], eu: Eu): T[] {
  return todos.filter(m => eu.modulos.includes(m.id))
}

/** A tela a abrir: a pedida no endereço, se a pessoa a tem; senão a primeira que ela tem; null sem nenhuma. */
export function telaAberta(modulos: readonly { id: string }[], pedida: string | null): string | null {
  return modulos.find(m => m.id === pedida)?.id ?? modulos[0]?.id ?? null
}
