// Quem entrou e quais módulos pode abrir (GET /api/eu, ninho/servidor/app.py), e como entrar e sair
// (POST /api/entrar e /api/sair). A casca lê quem entrou antes de montar as telas: a barra mostra só
// os módulos da pessoa (docs/architecture-v2/people-and-visibility.md, "The session").

import { ErroPedido, pedir } from './pedido'

/** `id` é o da pessoa em /api/pessoas: a tela dos desejos abre na parte dela. */
export type Eu = { id: string; nome: string; modulos: string[] }

export const lerEu = () => pedir<Eu>('GET', 'api/eu')

/** Quem entrou, ou null quando não há sessão (o 401 do servidor); qualquer outra recusa é erro. */
export const lerEuOuNinguem = () =>
  lerEu().catch((e: unknown) => {
    if (e instanceof ErroPedido && e.status === 401) return null
    throw e
  })

/** Troca nome e senha por uma sessão: true quando entrou, false quando o servidor diz que estão
 * errados (401, ou 422 para um campo fora da forma). O cookie da sessão é do navegador: a página
 * nunca o vê, e a senha não fica em lugar nenhum daqui. Não passa por `pedir`: um 401 aqui é a
 * senha errada, não uma sessão perdida. */
export async function entrar(nome: string, senha: string): Promise<boolean> {
  const r = await fetch('api/entrar', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ nome, senha }),
  })
  if (r.ok) return true
  if (r.status === 401 || r.status === 422) return false
  throw new ErroPedido(`Falha no pedido: HTTP ${r.status}`, r.status)
}

/** Encerra a sessão deste navegador no servidor. */
export const sair = () => pedir<void>('POST', 'api/sair')

/** Os módulos da casca que a pessoa tem, na ordem da casca. */
export function permitidos<T extends { id: string }>(todos: readonly T[], eu: Eu): T[] {
  return todos.filter(m => eu.modulos.includes(m.id))
}

/** A tela a abrir: a pedida no endereço, se a pessoa a tem; senão a primeira que ela tem; null sem nenhuma. */
export function telaAberta(modulos: readonly { id: string }[], pedida: string | null): string | null {
  return modulos.find(m => m.id === pedida)?.id ?? modulos[0]?.id ?? null
}
