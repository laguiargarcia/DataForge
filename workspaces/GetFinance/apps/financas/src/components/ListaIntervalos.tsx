import type { Intervalo } from '../conferencia'
import { SeloEstado } from './SeloEstado'

// intervalos null: ainda carregando.
type Props = { intervalos: Intervalo[] | null; ativo: string | null; onEscolher: (id: string) => void }

export function ListaIntervalos({ intervalos, ativo, onEscolher }: Readonly<Props>) {
  return (
    <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] py-6 px-4 box-border flex flex-col gap-1">
      <h2 className="mt-1 mr-0 mb-[10px] ml-4 text-[16px] font-semibold">Intervalos entre coletas</h2>
      {intervalos?.length === 0 && <span className="ml-4 text-[15px] text-secundario">Nenhum intervalo.</span>}
      {intervalos?.map(i => (
        <button
          key={i.id}
          className={`row w-full box-border min-h-[76px] py-[14px] px-4 rounded-[24px] flex flex-col justify-center gap-[6px] ${i.id === ativo ? 'bg-ativa' : ''}`}
          aria-pressed={i.id === ativo}
          onClick={() => onEscolher(i.id)}
        >
          <span className="flex justify-between gap-4 items-baseline">
            <span className="text-[15px] font-medium">{i.de} → {i.ate}</span>
            <span className={`text-[16px] font-semibold whitespace-nowrap ${i.fecha ? '' : 'text-alerta'}`}>{i.diferenca}</span>
          </span>
          <span className="flex gap-2 items-center flex-wrap text-[13px] text-secundario">
            <SeloEstado intervalo={i} />
            <span>{i.conta}</span>
            {i.quantos && <span>· {i.quantos}</span>}
          </span>
        </button>
      ))}
    </section>
  )
}
