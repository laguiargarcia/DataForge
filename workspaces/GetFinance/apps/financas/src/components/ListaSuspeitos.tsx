import type { Suspeito } from '../conferencia'

type Props = { suspeitos: Suspeito[]; vazio: string }

export function ListaSuspeitos({ suspeitos, vazio }: Readonly<Props>) {
  if (suspeitos.length === 0) return <span className="text-[15px] text-secundario">{vazio}</span>
  return (
    <div className="flex flex-col">
      {suspeitos.map(s => (
        <div key={s.id} className="flex justify-between gap-4 py-3 border-t border-linha">
          <div className="flex flex-col gap-[2px] min-w-0">
            <span className="text-[15px]">{s.descricao}</span>
            <span className="text-[13px] text-secundario">{s.data} · {s.conta}</span>
            <span className="text-[13px] text-alerta">{s.motivos.join(' · ')}</span>
          </div>
          <span className="text-[15px] font-semibold whitespace-nowrap">{s.valor}</span>
        </div>
      ))}
    </div>
  )
}
