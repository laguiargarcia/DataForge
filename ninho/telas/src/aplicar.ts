// "Aplicar": pede ao servidor o job que refaz as categorias e acompanha a execução até ela acabar.
import type { Execucao } from './api'

/** Quanto cada pedido de andamento segura a resposta: poucos pedidos, e um aviso a cada passo. */
export const ESPERA = 20

export type Motor = {
  pedirAplicar: () => Promise<Execucao>
  andamento: (runId: string, esperar: number) => Promise<Execucao>
}

/** Acabou quando o motor diz a hora do fim (vale para sucesso e falha). */
export const acabou = (e: Execucao) => e.finished_at != null

/** Pede o aplicar e segue a execução, avisando cada estado; devolve o estado final. */
export async function aplicar(motor: Motor, aoMudar: (e: Execucao) => void): Promise<Execucao> {
  let execucao = await motor.pedirAplicar()
  aoMudar(execucao)
  while (!acabou(execucao)) {
    execucao = await motor.andamento(execucao.run_id, ESPERA)
    aoMudar(execucao)
  }
  return execucao
}

/** O texto da faixa enquanto o aplicar anda, e quando ele falha. */
export function textoAndamento(e: Execucao): string {
  if (e.state === 'success') return 'Aplicado.'
  if (e.state === 'failed') {
    const onde = e.failed_task ? ` na tarefa ${e.failed_task}` : ''
    const motivo = e.reason ? `: ${e.reason}` : '.'
    return `O aplicar falhou${onde}${motivo}`
  }
  if (e.state === 'running') return 'Aplicando: recalculando as categorias…'
  return 'Aplicando: na fila…'
}
