// Quem entrou e quais módulos pode abrir (GET /api/eu, ninho/servidor/app.py). A casca lê isto antes
// de montar as telas: a barra mostra só os módulos da pessoa (docs/architecture-v2/people-and-visibility.md).

export type Eu = { nome: string; modulos: string[] }

export async function lerEu(): Promise<Eu> {
  const r = await fetch('api/eu')
  if (!r.ok) throw new Error(`Falha no pedido: HTTP ${r.status}`)
  return (await r.json()) as Eu
}

/** Os módulos da casca que a pessoa tem, na ordem da casca. */
export function permitidos<T extends { id: string }>(todos: readonly T[], eu: Eu): T[] {
  return todos.filter(m => eu.modulos.includes(m.id))
}

/** A tela a abrir: a pedida no endereço, se a pessoa a tem; senão a primeira que ela tem; null sem nenhuma. */
export function telaAberta(modulos: readonly { id: string }[], pedida: string | null): string | null {
  return modulos.find(m => m.id === pedida)?.id ?? modulos[0]?.id ?? null
}
