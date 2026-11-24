// O pedido ao servidor do ninho, comum a todos os módulos e à casca. A página vem do mesmo servidor,
// atrás do login da pessoa (HTTP Basic): o navegador manda o login em cada pedido, então nada aqui
// trata de senha. Os endereços são relativos à página, que o servidor entrega em "/".

/** Pedido recusado, com a mensagem que a tela mostra e o status HTTP. */
export class ErroPedido extends Error {
  readonly status: number

  constructor(mensagem: string, status: number) {
    super(mensagem)
    this.status = status
  }
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
    const detalhe = await r.json().then((c: { detail?: unknown }) => c.detail, () => undefined)
    throw new ErroPedido(typeof detalhe === 'string' ? detalhe : `Falha no pedido: HTTP ${r.status}`, r.status)
  }
  return (r.status === 204 ? undefined : await r.json()) as T
}
