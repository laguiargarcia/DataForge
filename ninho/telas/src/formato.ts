// Textos da tela: dinheiro, datas e meses (AAAA-MM, como a rota de transações pede).

const reais = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })
const nomeMes = new Intl.DateTimeFormat('pt-BR', { month: 'long', year: 'numeric', timeZone: 'UTC' })

/** R$ com 2 casas; o número vem em reais (amount_signed) e só é arredondado aqui. */
export const brl = (valor: number) => reais.format(valor)

/** "2026-06-30" vira "30/06". */
export const diaMes = (data: string) => `${data.slice(8, 10)}/${data.slice(5, 7)}`

const MES = /^\d{4}-(0[1-9]|1[0-2])$/

export const mesValido = (texto: string | null): texto is string => texto !== null && MES.test(texto)

/** O mês de uma data, no fuso de quem olha. */
export const mesDe = (hoje: Date) => `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, '0')}`

/** O mês `n` meses depois (ou antes, com n negativo). */
export function deslocar(mes: string, n: number): string {
  const total = Number(mes.slice(0, 4)) * 12 + Number(mes.slice(5, 7)) - 1 + n
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}`
}

/** "2026-06" vira "junho de 2026". */
export const rotuloMes = (mes: string) =>
  nomeMes.format(new Date(Date.UTC(Number(mes.slice(0, 4)), Number(mes.slice(5, 7)) - 1, 1)))

const CONTAS: Record<string, string> = { BANK: 'Conta', CREDIT: 'Cartão' }

/** O tipo de conta da tabela (Pluggy) em português; um tipo desconhecido aparece como veio. */
export const tipoConta = (tipo: string) => CONTAS[tipo] ?? tipo
