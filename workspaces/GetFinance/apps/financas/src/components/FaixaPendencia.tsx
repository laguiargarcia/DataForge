import { useEffect, useState } from 'react'
import { andamento, aplicar, faseDoAndamento, textoFaixa, type Faixa } from '../edicao'

const INTERVALO_MS = 1500

type Props = { pendente: boolean; onAtualizado: () => void }

// Some quando não há edição a recalcular. O Aplicar pede o reclassificar e acompanha a execução até
// o fim; no sucesso, o app recarrega os dados (onAtualizado).
export function FaixaPendencia({ pendente, onAtualizado }: Readonly<Props>) {
  const [execucao, setExecucao] = useState<string | null>(null)
  const [faixa, setFaixa] = useState<Faixa | null>(null) // null: segue `pendente`

  useEffect(() => {
    if (execucao === null) return
    let viva = true
    const consultar = () => andamento(execucao).then(
      a => {
        if (!viva) return
        const proxima = faseDoAndamento(a)
        if (proxima === 'terminou') {
          setExecucao(null)
          setFaixa(null)
          onAtualizado()
          return
        }
        setFaixa(proxima)
        if (proxima.fase === 'falhou') setExecucao(null)
      },
      (e: Error) => {
        if (!viva) return
        setExecucao(null)
        setFaixa({ fase: 'falhou', motivo: e.message })
      },
    )
    void consultar()
    const timer = setInterval(() => { void consultar() }, INTERVALO_MS)
    return () => { viva = false; clearInterval(timer) }
  }, [execucao, onAtualizado])

  const atual: Faixa = faixa ?? (pendente ? { fase: 'pendente' } : { fase: 'em_dia' })
  const texto = textoFaixa(atual)
  if (texto === null) return null

  const pedir = () => {
    setFaixa({ fase: 'rodando', status: 'pending' })
    aplicar().then(setExecucao, (e: Error) => setFaixa({ fase: 'falhou', motivo: e.message }))
  }
  const falhou = atual.fase === 'falhou'

  return (
    <output
      className={`rounded-[24px] px-6 py-3 flex items-center justify-between gap-4 flex-wrap ${falhou ? 'bg-alerta-fundo text-alerta' : 'bg-cartao'}`}
    >
      <span className="text-[15px] font-medium">{texto}</span>
      {atual.fase !== 'rodando' && (
        <button
          onClick={pedir}
          className="min-h-11 px-[18px] rounded-[999px] bg-acento text-cartao text-[15px] font-semibold flex items-center"
        >
          Aplicar
        </button>
      )}
    </output>
  )
}
