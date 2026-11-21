import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { lerTabela } from '../api'
import { montarConferencia, type Conferencia, type LinhaConferencia, type LinhaSuspeito } from '../conferencia'
import { CartaoConferencia } from './CartaoConferencia'
import { CartaoIntervalo } from './CartaoIntervalo'
import { FalhaCarga } from './FalhaCarga'
import { ListaIntervalos } from './ListaIntervalos'

type Estado = { fase: 'carregando' } | { fase: 'erro' } | { fase: 'ok'; conferencia: Conferencia }

// As duas tabelas vêm prontas do ETL (tasks conferencia_saldo e lancamentos_suspeitos do finance_daily).
async function carregar(): Promise<Conferencia> {
  const [intervalos, suspeitos] = await Promise.all([
    lerTabela<LinhaConferencia>('conferencia_saldo'),
    lerTabela<LinhaSuspeito>('lancamentos_suspeitos'),
  ])
  return montarConferencia(intervalos, suspeitos)
}

// aplicada: a versão do banco de edição que o último Aplicar levou ao curated; quando muda, a conferência
// é relida (uma marca de explicado só aparece depois do Aplicar). onMudou: uma marca foi salva ou desfeita.
type Props = { aplicada: number | null; onMudou: () => void }

export function TelaConferencia({ aplicada, onMudou }: Readonly<Props>) {
  const [estado, setEstado] = useState<Estado>({ fase: 'carregando' })
  const [ativo, setAtivo] = useState<string | null>(null)

  const abrir = useCallback((manter: string | null = null) => {
    setEstado({ fase: 'carregando' })
    setAtivo(manter)
    carregar().then(
      conferencia => setEstado({ fase: 'ok', conferencia }),
      () => setEstado({ fase: 'erro' }),
    )
  }, [])
  const [ultimaAplicada, setUltimaAplicada] = useState(aplicada)
  useEffect(() => { abrir() }, [abrir])
  useEffect(() => {
    if (aplicada === ultimaAplicada) return
    setUltimaAplicada(aplicada)
    abrir(ativo)
  }, [aplicada, ultimaAplicada, abrir, ativo])

  const conferencia = estado.fase === 'ok' ? estado.conferencia : null
  const intervalo = conferencia?.intervalos.find(i => i.id === ativo) ?? null

  let cartao: ReactNode = null
  if (estado.fase === 'erro') cartao = <FalhaCarga onTentar={() => abrir()} />
  else if (intervalo) cartao = <CartaoIntervalo intervalo={intervalo} onVoltar={() => setAtivo(null)} onMudou={onMudou} />
  else if (conferencia) cartao = <CartaoConferencia conferencia={conferencia} />

  return (
    <div className="flex flex-wrap gap-5 items-stretch">
      <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] p-8 box-border flex flex-col gap-7">
        {cartao}
      </section>

      <ListaIntervalos
        intervalos={conferencia?.intervalos ?? null}
        ativo={intervalo?.id ?? null}
        onEscolher={id => setAtivo(id === ativo ? null : id)}
      />
    </div>
  )
}
