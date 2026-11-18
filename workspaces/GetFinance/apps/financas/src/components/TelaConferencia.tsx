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

export function TelaConferencia() {
  const [estado, setEstado] = useState<Estado>({ fase: 'carregando' })
  const [ativo, setAtivo] = useState<string | null>(null)

  const abrir = useCallback(() => {
    setEstado({ fase: 'carregando' })
    setAtivo(null)
    carregar().then(
      conferencia => setEstado({ fase: 'ok', conferencia }),
      () => setEstado({ fase: 'erro' }),
    )
  }, [])
  useEffect(() => { abrir() }, [abrir])

  const conferencia = estado.fase === 'ok' ? estado.conferencia : null
  const intervalo = conferencia?.intervalos.find(i => i.id === ativo) ?? null

  let cartao: ReactNode = null
  if (estado.fase === 'erro') cartao = <FalhaCarga onTentar={abrir} />
  else if (intervalo) cartao = <CartaoIntervalo intervalo={intervalo} onVoltar={() => setAtivo(null)} />
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
