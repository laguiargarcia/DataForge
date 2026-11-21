// Conferência pelo saldo (df-z4fh.4) a partir de curated.conferencia_saldo e curated.lancamentos_suspeitos.
// As regras (o estado de cada intervalo, o motivo de cada suspeito) estão no ETL: este módulo só ordena,
// agrupa e formata. A API manda os instantes como texto ISO com fuso (2026-09-08T15:30:00+00:00, às vezes
// com fração de segundo) e os dias como YYYY-MM-DD; dias nunca vão para Date.
import { brl, brlSinal } from './resumo'

export type LinhaConferencia = {
  account_uuid: string
  de: string
  ate: string
  coletado_em: string | null
  saldo_de: number | null
  saldo_ate: number | null
  variacao_saldo: number | null
  soma_lancamentos: number | null
  n_lancamentos: number | null
  diferenca: number | null
  diferenca_acumulada: number | null
  estado: string
}
// Uma linha por lançamento e motivo; o lançamento fora de todo intervalo (cartão) vem com intervalo_* nulos.
export type LinhaSuspeito = {
  transaction_id: string
  account_uuid: string
  date: string | null
  amount: number | null
  description: string | null
  status: string | null
  motivo: string
  primeira_coleta: string | null
  ultima_coleta: string | null
  n_coletas: number | null
  intervalo_de: string | null
  intervalo_ate: string | null
  intervalo_estado: string | null
}

export type Suspeito = { id: string; data: string; descricao: string; valor: string; conta: string; motivos: string[] }
export type Intervalo = {
  id: string
  conta: string
  de: string
  ate: string
  variacao: string
  soma: string
  lancamentos: string
  diferenca: string
  estado: string
  rotuloEstado: string
  fecha: boolean
  suspeitos: Suspeito[]
  quantos: string
}
export type ContaAtual = { conta: string; diferenca: number | null; valor: string; ate: string; rotuloEstado: string }
export type Conferencia = { avisos: string[]; contas: ContaAtual[]; intervalos: Intervalo[]; semIntervalo: Suspeito[] }

// Os estados do ETL (scripts/report/conferencia_saldo.py): rótulo e se o intervalo fecha. Um estado
// desconhecido aparece como veio, entre os que não fecham.
const ESTADOS = new Map([
  ['fecha', { rotulo: 'Fecha', fecha: true, ordem: 2 }],
  ['fecha_com_vizinho', { rotulo: 'Fecha com o vizinho', fecha: true, ordem: 1 }],
  ['em_aberto', { rotulo: 'Em aberto', fecha: false, ordem: 0 }],
  // O primeiro intervalo da conta: a outra metade do par é de antes da primeira coleta de saldo. Explicado.
  ['historico_incompleto', { rotulo: 'Começo do histórico', fecha: true, ordem: 1 }],
  ['nao_fecha', { rotulo: 'Não fecha', fecha: false, ordem: 0 }],
])

// Os motivos do ETL (scripts/report/lancamentos_suspeitos.py), na ordem de lá.
const MOTIVOS = new Map([
  ['sumiu_da_coleta', 'Sumiu da coleta'],
  ['pendente_que_sumiu', 'Pendente que sumiu'],
  ['aplicacao_automatica', 'Aplicação automática'],
  ['uma_coleta_so', 'Veio numa coleta só'],
])
const ORDEM_MOTIVO = [...MOTIVOS.keys()]

const SEM_CONFERENCIA = 'A conferência pelo saldo ainda não foi calculada neste workspace.'
const SEM_SUSPEITOS = 'Os lançamentos suspeitos ainda não foram calculados neste workspace.'

// O instante na hora de Brasília: é a hora que o extrato do banco mostra.
const formatoInstante = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})

// Date.parse só garante três casas de fração de segundo; a API manda até seis.
const paraMs = (iso: string) => Date.parse(iso.replace(/(\.\d{3})\d+/, '$1'))

export function rotuloInstante(iso: string | null): string {
  return iso === null ? '—' : formatoInstante.format(paraMs(iso)).replace(', ', ' ')
}

const rotuloDia = (dia: string | null) => (dia === null ? '—' : `${dia.slice(8, 10)}/${dia.slice(5, 7)}/${dia.slice(0, 4)}`)
const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`
const chave = (conta: string, ate: string) => `${conta}|${ate}`
const estadoDe = (estado: string) => ESTADOS.get(estado) ?? { rotulo: estado, fecha: false, ordem: 0 }

// Compara códigos de caractere (não localeCompare): ids e datas YYYY-MM-DD não têm regra de idioma.
function porTexto(a: string, b: string): number {
  if (a === b) return 0
  return a < b ? -1 : 1
}

// Os que não fecham primeiro, depois os que fecham com o vizinho, depois os que fecham; em cada grupo, o
// mais novo primeiro e, no mesmo instante, pela conta.
function porEstadoDepoisMaisNovo(a: LinhaConferencia, b: LinhaConferencia): number {
  return estadoDe(a.estado).ordem - estadoDe(b.estado).ordem
    || paraMs(b.ate) - paraMs(a.ate)
    || porTexto(a.account_uuid, b.account_uuid)
}

// Um lançamento suspeito, com os códigos dos seus motivos e a chave do intervalo em que cai (ou null).
type Agrupado = { linha: LinhaSuspeito; intervalo: string | null; motivos: string[] }

const posicaoMotivo = (m: string) => (MOTIVOS.has(m) ? ORDEM_MOTIVO.indexOf(m) : ORDEM_MOTIVO.length)

function paraSuspeito({ linha, motivos }: Agrupado): Suspeito {
  return {
    id: linha.transaction_id,
    data: rotuloDia(linha.date),
    descricao: linha.description ?? '—',
    valor: brl(linha.amount),
    conta: linha.account_uuid,
    motivos: [...motivos]
      .sort((a, b) => posicaoMotivo(a) - posicaoMotivo(b) || porTexto(a, b))
      .map(m => MOTIVOS.get(m) ?? m),
  }
}

// Um suspeito por lançamento, com todos os seus motivos; mais novo primeiro e, no mesmo dia, pelo id.
function agrupar(linhas: LinhaSuspeito[]): Agrupado[] {
  const porId = new Map<string, Agrupado>()
  for (const linha of linhas) {
    const g = porId.get(linha.transaction_id)
    if (g) g.motivos.push(linha.motivo)
    else {
      const intervalo = linha.intervalo_ate === null ? null : chave(linha.account_uuid, linha.intervalo_ate)
      porId.set(linha.transaction_id, { linha, intervalo, motivos: [linha.motivo] })
    }
  }
  return [...porId.values()].sort((a, b) =>
    porTexto(b.linha.date ?? '', a.linha.date ?? '') || porTexto(a.linha.transaction_id, b.linha.transaction_id))
}

function contasAtuais(linhas: LinhaConferencia[]): ContaAtual[] {
  const maisNovo = new Map<string, LinhaConferencia>()
  for (const l of linhas) {
    const atual = maisNovo.get(l.account_uuid)
    if (!atual || paraMs(l.ate) > paraMs(atual.ate)) maisNovo.set(l.account_uuid, l)
  }
  return [...maisNovo.values()]
    .sort((a, b) => porTexto(a.account_uuid, b.account_uuid))
    .map(l => ({
      conta: l.account_uuid,
      diferenca: l.diferenca_acumulada,
      valor: brlSinal(l.diferenca_acumulada),
      ate: rotuloInstante(l.ate),
      rotuloEstado: estadoDe(l.estado).rotulo,
    }))
}

export function montarConferencia(
  linhas: LinhaConferencia[] | null,
  suspeitos: LinhaSuspeito[] | null,
): Conferencia {
  const avisos = [
    ...(linhas === null ? [SEM_CONFERENCIA] : []),
    ...(suspeitos === null ? [SEM_SUSPEITOS] : []),
  ]
  const conferencia = linhas ?? []

  const doIntervalo = new Map<string, Suspeito[]>(conferencia.map(l => [chave(l.account_uuid, l.ate), []]))
  const semIntervalo: Suspeito[] = []
  for (const g of agrupar(suspeitos ?? [])) {
    // Sem intervalo (cartão) ou de um intervalo que a conferência não tem: à parte, para não sumir.
    const destino = (g.intervalo === null ? undefined : doIntervalo.get(g.intervalo)) ?? semIntervalo
    destino.push(paraSuspeito(g))
  }

  const intervalos = [...conferencia].sort(porEstadoDepoisMaisNovo).map((l): Intervalo => {
    const id = chave(l.account_uuid, l.ate)
    const estado = estadoDe(l.estado)
    const lista = doIntervalo.get(id) ?? []
    return {
      id,
      conta: l.account_uuid,
      de: rotuloInstante(l.de),
      ate: rotuloInstante(l.ate),
      variacao: brl(l.variacao_saldo),
      soma: brl(l.soma_lancamentos),
      lancamentos: plural(l.n_lancamentos ?? 0, 'lançamento', 'lançamentos'),
      diferenca: brlSinal(l.diferenca),
      estado: l.estado,
      rotuloEstado: estado.rotulo,
      fecha: estado.fecha,
      suspeitos: lista,
      quantos: lista.length ? plural(lista.length, 'suspeito', 'suspeitos') : '',
    }
  })

  return { avisos, contas: contasAtuais(conferencia), intervalos, semIntervalo }
}
