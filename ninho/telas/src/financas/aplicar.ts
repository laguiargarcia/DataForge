// "Aplicar": pede ao servidor o job que refaz as categorias e acompanha a execução até ela acabar.
// Um aplicar em curso nunca vira dois (df-8x0m.23.13.10): o Aplicador recusa um segundo pedido
// enquanto o primeiro vive, um erro ao acompanhar volta a acompanhar o mesmo id, e o id fica guardado
// no aparelho até o fim, para a página recarregada retomar a mesma execução.
import { ErroPedido } from '../pedido'
import { type Execucao } from './api'

/** Quanto cada pedido de andamento segura a resposta: poucos pedidos, e um aviso a cada passo. */
export const ESPERA = 20
/** Quanto esperar (ms) antes de tentar acompanhar de novo, depois de um erro ao acompanhar. */
export const PAUSA = 3000

export type Motor = {
  pedirAplicar: () => Promise<Execucao>
  andamento: (runId: string, esperar: number) => Promise<Execucao>
}

/** Onde mora o id da execução em curso, da resposta do pedido até o estado final. */
export type Guarda = {
  ler: () => string | null
  guardar: (runId: string) => void
  esquecer: () => void
}

export type Avisos = {
  aoMudar: (e: Execucao) => void
  /** O acompanhar caiu; a execução segue e o Aplicador tenta de novo, com o mesmo id. */
  aoPerderContato: (erro: unknown) => void
}

export type Fim =
  | { tipo: 'acabou'; execucao: Execucao }
  | { tipo: 'naoComecou'; erro: unknown }
  /** O id guardado não é de uma execução que o servidor conheça. */
  | { tipo: 'esquecida' }

/** Acabou quando o motor diz a hora do fim (vale para sucesso e falha). */
export const acabou = (e: Execucao) => e.finished_at != null

export const mensagem = (e: unknown) => (e instanceof Error ? e.message : String(e))

const dormirMs = (ms: number) => new Promise<void>(r => setTimeout(r, ms))

// 404: o servidor não conhece o id; 422: o guardado nem é um id. Os dois não têm o que acompanhar.
const desconhecida = (erro: unknown) => erro instanceof ErroPedido && (erro.status === 404 || erro.status === 422)

export class Aplicador {
  private emCurso = false

  constructor(
    private readonly motor: Motor,
    private readonly guarda: Guarda,
    private readonly avisos: Avisos,
    private readonly dormir: (ms: number) => Promise<void> = dormirMs,
  ) {}

  /** Pede uma execução e a segue até o fim; `null`, sem pedir nada, quando uma já está em curso. */
  iniciar(): Promise<Fim> | null {
    return this.travado(async () => {
      let execucao: Execucao
      try {
        execucao = await this.motor.pedirAplicar()
      } catch (erro) {
        return { tipo: 'naoComecou', erro }
      }
      this.avisos.aoMudar(execucao)
      if (acabou(execucao)) return { tipo: 'acabou', execucao }
      this.guarda.guardar(execucao.run_id)
      return this.seguir(execucao.run_id, ESPERA)
    })
  }

  /** Volta a seguir a execução do id guardado (página recarregada); `null` sem id ou se já segue uma. */
  retomar(): Promise<Fim> | null {
    const runId = this.guarda.ler()
    if (runId === null) return null
    return this.travado(() => this.seguir(runId, 0))
  }

  private travado(trabalho: () => Promise<Fim>): Promise<Fim> | null {
    if (this.emCurso) return null
    this.emCurso = true
    return trabalho().finally(() => { this.emCurso = false })
  }

  private async seguir(runId: string, primeiraEspera: number): Promise<Fim> {
    let espera = primeiraEspera
    for (;;) {
      let execucao: Execucao
      try {
        execucao = await this.motor.andamento(runId, espera)
      } catch (erro) {
        if (desconhecida(erro)) {
          this.guarda.esquecer()
          return { tipo: 'esquecida' }
        }
        this.avisos.aoPerderContato(erro)
        await this.dormir(PAUSA)
        continue
      }
      this.avisos.aoMudar(execucao)
      if (acabou(execucao)) {
        this.guarda.esquecer()
        return { tipo: 'acabou', execucao }
      }
      espera = ESPERA
    }
  }
}

/** A guarda no localStorage: sobrevive a recarregar, a fechar e reabrir a aba e vale para as outras
 * abas da mesma tela. Sem armazenamento (aba privada, bloqueado), só não lembra. */
export function guardaLocal(chave: string): Guarda {
  const tentar = <T>(fazer: () => T, senao: T): T => {
    try { return fazer() } catch { return senao }
  }
  return {
    ler: () => tentar(() => localStorage.getItem(chave), null),
    guardar: runId => tentar(() => localStorage.setItem(chave, runId), undefined),
    esquecer: () => tentar(() => localStorage.removeItem(chave), undefined),
  }
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

/** A faixa enquanto o acompanhar está sem contato: o aplicar não parou. */
export const textoSemContato = (erro: unknown) =>
  `O aplicar segue, mas o servidor não respondeu (${mensagem(erro)}). Tentando acompanhar de novo…`

/** O aviso que fica depois do fim; `null` quando não há o que avisar. */
export function textoFim(fim: Fim): string | null {
  if (fim.tipo === 'naoComecou') return `O aplicar não começou: ${mensagem(fim.erro)}`
  if (fim.tipo === 'acabou' && fim.execucao.state !== 'success') return textoAndamento(fim.execucao)
  return null
}
