// O pedido ao servidor do ninho, comum a todos os módulos e à casca. A página vem do mesmo servidor,
// e quem entrou tem uma sessão num cookie que o navegador manda sozinho em cada pedido: nada aqui
// trata de senha. Os endereços são relativos à página, que o servidor entrega em "/".
// Um 401 quer dizer que não há sessão (nunca houve, venceu ou foi encerrada): além de recusar o
// pedido, avisa a casca, que troca as telas pelo formulário de login (df-8x0m.23.31.5).

/** Pedido recusado, com a mensagem que a tela mostra e o status HTTP. */
export class ErroPedido extends Error {
  readonly status: number

  constructor(mensagem: string, status: number) {
    super(mensagem)
    this.status = status
  }
}

let semSessao: (() => void) | null = null

/** A casca diz o que fazer quando um pedido volta sem sessão; devolve como parar de ouvir. */
export function aoFicarSemSessao(aviso: () => void): () => void {
  semSessao = aviso
  return () => { if (semSessao === aviso) semSessao = null }
}

// Um id vira um segmento só: "/" e também "." viram %XX, para "." e ".." não serem lidos como
// segmentos de ponto e levarem o pedido a outra rota.
export const segmento = (valor: string) => encodeURIComponent(valor).replaceAll('.', '%2E')

/** Faz o pedido e devolve o corpo JSON (nada num 204); recusado, lança ErroPedido com a mensagem
 * do servidor (`detail`) ou, sem ela, o status. */
export async function pedir<T>(metodo: string, endereco: string, corpo?: unknown): Promise<T> {
  const opcoes: RequestInit = corpo === undefined
    ? { method: metodo }
    : { method: metodo, headers: { 'content-type': 'application/json' }, body: JSON.stringify(corpo) }
  const r = await fetch(endereco, opcoes)
  if (!r.ok) {
    if (r.status === 401) semSessao?.()
    const detalhe = await r.json().then((c: { detail?: unknown }) => c.detail, () => undefined)
    throw new ErroPedido(typeof detalhe === 'string' ? detalhe : `Falha no pedido: HTTP ${r.status}`, r.status)
  }
  return (r.status === 204 ? undefined : await r.json()) as T
}
