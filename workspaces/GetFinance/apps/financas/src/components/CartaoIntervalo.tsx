import type { Intervalo } from '../conferencia'
import { Faixa } from './CartaoResumo'
import { ListaSuspeitos } from './ListaSuspeitos'
import { SeloEstado } from './SeloEstado'

type Props = { intervalo: Intervalo; onVoltar: () => void }

export function CartaoIntervalo({ intervalo, onVoltar }: Readonly<Props>) {
  return (
    <div className="flex flex-col gap-5">
      <button
        onClick={onVoltar}
        className="self-start min-h-11 pl-3 pr-[18px] rounded-[999px] bg-faixa text-acento text-[15px] font-semibold flex items-center gap-[6px]"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
        Conferência
      </button>
      <div className="flex flex-col gap-2">
        <span className="text-[16px] text-secundario">{intervalo.conta} · {intervalo.de} → {intervalo.ate}</span>
        <span className={`text-[clamp(40px,5vw,56px)] leading-none font-semibold tracking-[-0.03em] ${intervalo.fecha ? 'text-acento' : 'text-alerta'}`}>{intervalo.diferenca}</span>
        <span><SeloEstado intervalo={intervalo} /></span>
      </div>
      <div className="flex flex-col gap-2">
        <Faixa rotulo="Variação do saldo" valor={intervalo.variacao} />
        <Faixa rotulo={`Soma dos lançamentos (${intervalo.lancamentos})`} valor={intervalo.soma} />
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-[16px] font-semibold">Suspeitos</h3>
        <ListaSuspeitos suspeitos={intervalo.suspeitos} vazio="Nenhum lançamento suspeito neste intervalo." />
      </div>
    </div>
  )
}
