// A tela Extratos (df-7xk9.3): o PDF do extrato do banco sobe pelo backend do app (POST /extratos), que o
// confere, guarda em data/extratos e pede o job extrato; a tela acompanha o job até o veredito refeito.
import { ErroEdicao, pedir, type Andamento } from './edicao'
import { dataCurta } from './transacoes'

/** Datas ISO (AAAA-MM-DD); nulas quando o arquivo guardado à mão não deixou dia nas tabelas. */
export type Periodo = { de: string | null; ate: string | null }
export type Guardado = {
  arquivo: string
  periodo: Periodo
  emitido_em: string | null
  n_lancamentos: number
  n_saldos: number
  linhas_ignoradas: number
}
/** A resposta 201 do POST /extratos. */
export type Aceite = {
  arquivo: string
  periodo: { de: string; ate: string }
  n_lancamentos: number
  n_saldos: number
  linhas_ignoradas: number
  dias_divergentes: string[]
  execution_id: string
}

/** O job extrato pedido pelo envio, de pendente até concluído ou falhou. */
export type Job =
  | { fase: 'rodando'; status: 'pending' | 'running' }
  | { fase: 'concluido' }
  | { fase: 'falhou'; motivo: string | null }

/** O resultado do envio de um PDF. */
export type Envio =
  | { fase: 'enviando' }
  | { fase: 'aceito'; arquivo: string; periodo: string; contagens: string; divergentes: string | null; execucao: string; job: Job }
  | { fase: 'recusado'; motivo: string }   // 422, 409, 413: nada foi guardado
  | { fase: 'guardado'; motivo: string }   // 502: o PDF foi guardado, mas o job extrato não foi pedido
  | { fase: 'erro'; motivo: string }       // outra falha (rede, 500): o envio não se sabe aceito

const RECUSAS = new Set([409, 413, 422])

const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`

const lista = (itens: string[]) => (itens.length === 1 ? itens[0] : `${itens.slice(0, -1).join(', ')} e ${itens.at(-1)}`)

export const listarExtratos = async () => (await pedir<{ arquivos: Guardado[] }>('GET', 'extratos')).arquivos

export function rotuloPeriodo(p: Periodo): string {
  return p.de === null || p.ate === null ? 'Sem dias nas tabelas' : `${dataCurta(p.de)} a ${dataCurta(p.ate)}`
}

const contagens = (g: Pick<Guardado, 'n_lancamentos' | 'n_saldos' | 'linhas_ignoradas'>) =>
  `${plural(g.n_lancamentos, 'lançamento', 'lançamentos')} · ${plural(g.n_saldos, 'saldo', 'saldos')} · ` +
  plural(g.linhas_ignoradas, 'linha ignorada', 'linhas ignoradas')

export const rotuloGuardado = (g: Guardado) => ({ periodo: rotuloPeriodo(g.periodo), contagens: contagens(g) })

function textoDivergentes(dias: string[]): string | null {
  if (dias.length === 0) return null
  const datas = lista(dias.map(dataCurta))
  const frase = dias.length === 1
    ? `O saldo de ${datas} difere do de um extrato já guardado.`
    : `Os saldos de ${datas} diferem dos de extratos já guardados.`
  return `${frase} O extrato foi aceito mesmo assim.`
}

function aceito(r: Aceite): Envio {
  return {
    fase: 'aceito', arquivo: r.arquivo, periodo: rotuloPeriodo(r.periodo), contagens: contagens(r),
    divergentes: textoDivergentes(r.dias_divergentes), execucao: r.execution_id,
    job: { fase: 'rodando', status: 'pending' },
  }
}

/** Sobe o PDF cru (o backend lê Content-Type application/pdf, sem multipart) e diz o que houve. */
export async function enviarExtrato(arquivo: Blob): Promise<Envio> {
  try {
    return aceito(await pedir<Aceite>('POST', 'extratos', arquivo.slice(0, arquivo.size, 'application/pdf')))
  } catch (e) {
    const motivo = (e as Error).message
    if (e instanceof ErroEdicao && RECUSAS.has(e.status)) return { fase: 'recusado', motivo }
    if (e instanceof ErroEdicao && e.status === 502) return { fase: 'guardado', motivo }
    return { fase: 'erro', motivo }
  }
}

export function textoEnvio(envio: Envio): string {
  switch (envio.fase) {
    case 'enviando': return 'Enviando…'
    case 'aceito': return `Extrato aceito: ${envio.periodo}.`
    case 'recusado': return `Extrato recusado: ${envio.motivo}.`
    // Reenviar o mesmo PDF daria 409 (ele já está guardado): a tela não oferece tentar de novo.
    case 'guardado': return `${envio.motivo}. O veredito não foi refeito agora: ele sai na próxima rodada diária.`
    case 'erro': return `Não foi possível enviar o extrato: ${envio.motivo}.`
  }
}

/** Próxima etapa do job extrato a partir do andamento da execução (GET aplicar/{id}). */
export function etapaDoJob(a: Andamento): Job {
  if (a.status === 'success') return { fase: 'concluido' }
  if (a.status === 'pending' || a.status === 'running') return { fase: 'rodando', status: a.status }
  return { fase: 'falhou', motivo: a.reason }
}

export function textoJob(job: Job): string {
  switch (job.fase) {
    case 'rodando': return job.status === 'pending' ? 'Na fila: esperando outro job do workspace terminar…' : 'Refazendo o veredito…'
    case 'concluido': return 'Veredito refeito: a Conferência já mostra este extrato.'
    case 'falhou': {
      const motivo = job.motivo ? ': ' + job.motivo : ''
      return `O job extrato falhou${motivo}. O PDF continua guardado.`
    }
  }
}
