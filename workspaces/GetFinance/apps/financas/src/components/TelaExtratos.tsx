import { type ChangeEvent, useCallback, useEffect, useState } from 'react'
import { andamento } from '../edicao'
import { enviarExtrato, etapaDoJob, listarExtratos, rotuloGuardado, textoEnvio, textoJob, type Envio, type Guardado, type Job } from '../extratos'
import { FalhaCarga } from './FalhaCarga'

const INTERVALO_MS = 1500

type Estado = { fase: 'carregando' } | { fase: 'erro' } | { fase: 'ok'; arquivos: Guardado[] }

// onMudou: o job extrato terminou e as tabelas do ETL (conferência, suspeitos) mudaram.
type Props = { onMudou: () => void }

export function TelaExtratos({ onMudou }: Readonly<Props>) {
  const [estado, setEstado] = useState<Estado>({ fase: 'carregando' })
  const [envio, setEnvio] = useState<Envio | null>(null)

  const abrir = useCallback(() => {
    setEstado({ fase: 'carregando' })
    listarExtratos().then(arquivos => setEstado({ fase: 'ok', arquivos }), () => setEstado({ fase: 'erro' }))
  }, [])
  useEffect(() => { abrir() }, [abrir])

  // Acompanha o job extrato pedido pelo envio aceito; no fim, a lista (curated.extrato_arquivos) e o
  // app recarregam.
  const execucao = envio?.fase === 'aceito' && envio.job.fase === 'rodando' ? envio.execucao : null
  useEffect(() => {
    if (execucao === null) return
    let viva = true
    const seguir = (job: Job) => {
      if (!viva) return
      setEnvio(e => (e?.fase === 'aceito' && e.execucao === execucao ? { ...e, job } : e))
      if (job.fase === 'concluido') { abrir(); onMudou() }
    }
    const consultar = () => andamento(execucao).then(
      a => seguir(etapaDoJob(a)),
      (e: Error) => seguir({ fase: 'falhou', motivo: e.message }),
    )
    void consultar()
    const timer = setInterval(() => { void consultar() }, INTERVALO_MS)
    return () => { viva = false; clearInterval(timer) }
  }, [execucao, abrir, onMudou])

  const escolher = (evento: ChangeEvent<HTMLInputElement>) => {
    const arquivo = evento.target.files?.[0]
    evento.target.value = '' // escolher o mesmo arquivo de novo também dispara
    if (!arquivo) return
    setEnvio({ fase: 'enviando' })
    void enviarExtrato(arquivo).then(setEnvio)
  }
  const enviando = envio?.fase === 'enviando'
  const problema = envio?.fase === 'recusado' || envio?.fase === 'erro'
    || (envio?.fase === 'aceito' && envio.job.fase === 'falhou')

  return (
    <div className="flex flex-wrap gap-5 items-stretch">
      <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] p-8 box-border flex flex-col gap-7">
        <div className="flex flex-col gap-3">
          <h2 className="m-0 text-[16px] font-semibold">Subir extrato</h2>
          <span className="text-[15px] text-secundario">
            O PDF do extrato da conta corrente, como o app do banco exporta. Aceito, ele é guardado e o veredito da
            Conferência é refeito com ele.
          </span>
        </div>
        <label
          className={`self-start min-h-11 px-[18px] rounded-[999px] bg-acento text-cartao text-[15px] font-semibold flex items-center ${enviando ? 'opacity-60' : 'cursor-pointer'}`}
        >
          <input type="file" accept=".pdf,application/pdf" className="sr-only" disabled={enviando} onChange={escolher} />
          Escolher PDF
        </label>
        {envio && (
          <output className={`rounded-[20px] py-[14px] px-[18px] flex flex-col gap-2 text-[15px] ${problema ? 'bg-alerta-fundo text-alerta' : 'bg-faixa'}`}>
            <span className="font-medium">{textoEnvio(envio)}</span>
            {envio.fase === 'aceito' && (
              <>
                <span className="text-secundario">{envio.contagens} · guardado como {envio.arquivo}</span>
                {envio.divergentes && <span className="text-alerta">{envio.divergentes}</span>}
                <span className={envio.job.fase === 'falhou' ? '' : 'text-secundario'}>{textoJob(envio.job)}</span>
              </>
            )}
          </output>
        )}
      </section>

      <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] py-6 px-4 box-border flex flex-col gap-1">
        <h2 className="mt-1 mr-0 mb-[10px] ml-4 text-[16px] font-semibold">Extratos guardados</h2>
        {estado.fase === 'erro' && <div className="ml-4"><FalhaCarga onTentar={abrir} /></div>}
        {estado.fase === 'ok' && estado.arquivos.length === 0 && (
          <span className="ml-4 text-[15px] text-secundario">Nenhum extrato guardado.</span>
        )}
        {estado.fase === 'ok' && estado.arquivos.map(g => {
          const { periodo, contagens } = rotuloGuardado(g)
          return (
            <div key={g.arquivo} className="min-h-[76px] py-[14px] px-4 rounded-[24px] flex flex-col justify-center gap-[6px]">
              <span className="text-[15px] font-medium">{periodo}</span>
              <span className="text-[13px] text-secundario">{contagens}</span>
            </div>
          )
        })}
      </section>
    </div>
  )
}
