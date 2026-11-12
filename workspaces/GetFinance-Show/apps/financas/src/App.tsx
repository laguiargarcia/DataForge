import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { lerTabela } from './api'
import {
  brl, brlSinal, meses, resumirMes, rotuloMes,
  type Cartao, type Dados, type Investimentos, type Liquidez,
  type SaudeCategoria, type SaudeMes, type TxClassificada,
} from './resumo'
import { CartaoResumo } from './components/CartaoResumo'
import { CartaoDetalhe } from './components/CartaoDetalhe'
import { ListaCategorias } from './components/ListaCategorias'

type Estado = { fase: 'carregando' } | { fase: 'erro' } | { fase: 'ok'; dados: Dados }

// Tudo vem pronto do ETL (tasks classify e report do finance_daily).
async function carregar(): Promise<Dados> {
  const [saude, saudeCategoria, transacoes, liquidez, investimentos, cartao] = await Promise.all([
    lerTabela<SaudeMes>('report_saude'),
    lerTabela<SaudeCategoria>('report_saude_categoria'),
    lerTabela<TxClassificada>('fact_transaction_classified'),
    lerTabela<Liquidez>('report_liquidity'),
    lerTabela<Investimentos>('report_investimentos'),
    lerTabela<Cartao>('report_cartao'),
  ])
  return { saude, saudeCategoria, transacoes, liquidez, investimentos, cartao }
}

const Seta = ({ d }: { d: string }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
)
const classeSeta = 'w-11 h-11 rounded-[999px] flex items-center justify-center text-secundario'

export function App() {
  const [estado, setEstado] = useState<Estado>({ fase: 'carregando' })
  const [mes, setMes] = useState<string | null>(null)
  const [selecionada, setSelecionada] = useState<string | null>(null)

  const abrir = useCallback(() => {
    setEstado({ fase: 'carregando' })
    carregar().then(
      dados => {
        setEstado({ fase: 'ok', dados })
        const lista = meses(dados)
        setMes(lista.at(-1) ?? null)
        setSelecionada(null)
      },
      () => setEstado({ fase: 'erro' }),
    )
  }, [])
  useEffect(() => { abrir() }, [abrir])

  const dados = estado.fase === 'ok' ? estado.dados : null
  const lista = useMemo(() => (dados ? meses(dados) : []), [dados])
  const calcular = (m: string) => resumirMes(m, dados!)
  const resumo = useMemo(
    () => (dados && mes ? calcular(mes) : null),
    [dados, mes],
  )
  const categoria = resumo?.categorias.find(c => c.id === selecionada) ?? null

  const indice = mes ? lista.indexOf(mes) : -1
  const irPara = (novo: string) => {
    setMes(novo)
    if (selecionada && !calcular(novo).categorias.some(c => c.id === selecionada)) setSelecionada(null)
  }
  const escolher = (id: string) => setSelecionada(id === selecionada ? null : id)

  const semDados = dados !== null && resumo === null
  const vazio = semDados ? '—' : ''
  const valor = (v: number | null, f: (x: number | null) => string = brl) =>
    resumo ? f(v) : vazio

  let principal: ReactNode
  if (estado.fase === 'erro') {
    principal = (
      <div className="flex flex-col gap-5 items-start">
        <span className="text-[16px] text-secundario">Não foi possível carregar os dados.</span>
        <button
          onClick={abrir}
          className="min-h-11 px-[18px] rounded-[999px] bg-faixa text-acento text-[15px] font-semibold flex items-center"
        >
          Tentar de novo
        </button>
      </div>
    )
  } else if (categoria) {
    principal = <CartaoDetalhe categoria={categoria} onVoltar={() => setSelecionada(null)} />
  } else {
    principal = (
      <CartaoResumo
        sobrou={valor(resumo?.sobrou ?? null, brlSinal)}
        entrou={valor(resumo?.entrou ?? null)}
        saiu={valor(resumo?.saiu ?? null)}
        naConta={valor(resumo?.naConta ?? null)}
        investido={valor(resumo?.investido ?? null)}
        fatura={valor(resumo?.fatura ?? null)}
      />
    )
  }

  return (
    <div className="min-h-[980px] bg-fundo text-texto font-sans pt-8 px-4 pb-16 box-border">
      <div className="max-w-[1120px] mx-auto flex flex-col gap-5">
        <header className="flex items-center justify-between gap-4 flex-wrap">
          <div className="text-[18px] font-semibold">Finanças</div>
          <div className="flex items-center gap-1 bg-cartao rounded-[999px] p-1">
            <button
              className={classeSeta}
              aria-label="Mês anterior"
              disabled={indice <= 0}
              onClick={() => irPara(lista[indice - 1])}
            >
              <Seta d="M15 6l-6 6 6 6" />
            </button>
            <div className="text-[15px] font-medium px-2">{mes ? rotuloMes(mes) : vazio}</div>
            <button
              className={classeSeta}
              aria-label="Próximo mês"
              disabled={indice < 0 || indice >= lista.length - 1}
              onClick={() => irPara(lista[indice + 1])}
            >
              <Seta d="M9 6l6 6-6 6" />
            </button>
          </div>
        </header>

        <div className="flex flex-wrap gap-5 items-stretch">
          <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] p-8 box-border flex flex-col gap-7">
            {principal}
          </section>

          <ListaCategorias categorias={resumo?.categorias ?? []} ativa={categoria?.id ?? null} onEscolher={escolher} />
        </div>
      </div>
    </div>
  )
}
