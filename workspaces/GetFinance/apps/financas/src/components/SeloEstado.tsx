import type { Intervalo } from '../conferencia'

type Props = { intervalo: Pick<Intervalo, 'estado' | 'rotuloEstado' | 'fecha'> }

// Não fecha em alerta; fecha com o vizinho no acento, para não se confundir com o que fecha sozinho.
function cores({ estado, fecha }: Props['intervalo']): string {
  if (!fecha) return 'bg-alerta-fundo text-alerta'
  if (estado === 'fecha_com_vizinho') return 'bg-ativa text-acento'
  return 'bg-faixa text-secundario'
}

export function SeloEstado({ intervalo }: Readonly<Props>) {
  return (
    <span className={`inline-flex items-center px-[10px] py-[2px] rounded-[999px] text-[13px] font-semibold whitespace-nowrap ${cores(intervalo)}`}>
      {intervalo.rotuloEstado}
    </span>
  )
}
