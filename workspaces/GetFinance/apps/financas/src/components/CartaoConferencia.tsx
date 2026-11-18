import type { Conferencia } from '../conferencia'
import { ListaSuspeitos } from './ListaSuspeitos'

type Props = { conferencia: Conferencia }

export function CartaoConferencia({ conferencia }: Readonly<Props>) {
  const { avisos, contas, semIntervalo } = conferencia
  return (
    <div className="flex flex-col gap-7">
      {avisos.map(a => (
        <div key={a} className="bg-faixa rounded-[20px] py-[14px] px-[18px] text-[15px] text-secundario">{a}</div>
      ))}
      {contas.map(c => (
        <div key={c.conta} className="flex flex-col gap-3">
          <span className="text-[16px] text-secundario">Diferença acumulada · {c.conta}</span>
          <span className="text-[clamp(44px,6vw,68px)] leading-none font-semibold tracking-[-0.03em] text-acento">{c.valor}</span>
          <span className="text-[15px] text-secundario">Até {c.ate} · {c.rotuloEstado}</span>
        </div>
      ))}
      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-[16px] font-semibold">Suspeitos sem intervalo</h3>
        <span className="text-[13px] text-secundario">Cartão e lançamentos fora dos intervalos da conferência.</span>
        <ListaSuspeitos suspeitos={semIntervalo} vazio="Nenhum." />
      </div>
      <span className="text-[13px] text-secundario mt-auto">Horários de Brasília.</span>
    </div>
  )
}
