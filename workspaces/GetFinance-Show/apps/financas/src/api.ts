type Resposta<T> = { columns: string[]; rows: T[]; truncated: boolean }

function endereco(tabela: string): string {
  const m = /\/apps\/([^/]+)\//.exec(location.pathname)
  if (m) {
    return `../../../api/workspaces/${encodeURIComponent(decodeURIComponent(m[1]))}/tables/${tabela}`
  }
  const ws = import.meta.env.VITE_WORKSPACE || 'GetFinance-Show'
  return `/api/workspaces/${encodeURIComponent(ws)}/tables/${tabela}`
}

export async function lerTabela<T>(tabela: string): Promise<T[] | null> {
  const r = await fetch(endereco(tabela))
  if (r.status === 404) return null
  if (!r.ok) throw new Error(`Falha ao ler ${tabela}: HTTP ${r.status}`)
  const corpo = (await r.json()) as Resposta<T>
  if (corpo.truncated) throw new Error(`Tabela ${tabela} cortada pelo servidor`)
  return corpo.rows
}
