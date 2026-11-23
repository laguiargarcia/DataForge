import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { lerTabela } from './api'
import {
  brl, brlSinal, meses, resumirMes, rotuloMes,
  type Cartao, type Dados, type Investimentos, type Liquidez,
  type SaudeCategoria, type SaudeMes, type TxClassificada,
} from './resumo'
import { CartaoResumo } from './components/CartaoResumo'
import { CartaoDetalhe } from './components/CartaoDetalhe'
import { ListaCategorias } from './components/ListaCategorias'
import { FalhaCarga } from './components/FalhaCarga'
import { TelaConferencia } from './components/TelaConferencia'
import { TelaExtratos } from './components/TelaExtratos'
import { TelaCategorias } from './components/TelaCategorias'
import { TelaTransacoes } from './components/TelaTransacoes'
import { FaixaPendencia } from './components/FaixaPendencia'
import { lerEdicao, type Edicao } from './edicao'

type Estado = { fase: 'carregando' } | { fase: 'erro' } | { fase: 'ok'; dados: Dados }
type Tela = 'resumo' | 'conferencia' | 'extratos' | 'categorias' | 'transacoes'
type EstadoEdicao = { fase: 'carregando' } | { fase: 'erro' } | { fase: 'ok'; edicao: Edicao }

const TELAS: [Tela, string][] = [['resumo', 'Resumo'], ['conferencia', 'Conferência'], ['extratos', 'Extratos'], ['categorias', 'Categorias'], ['transacoes', 'Transações']]

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
  const [tela, setTela] = useState<Tela>('resumo')
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

  // O banco de edição (backend do app). Recarrega depois de cada gravação, para a faixa de
  // pendência e as tabelas mostrarem o que está no banco.
  const [estadoEdicao, setEstadoEdicao] = useState<EstadoEdicao>({ fase: 'carregando' })
  const lerBanco = useCallback(() => {
    lerEdicao().then(edicao => setEstadoEdicao({ fase: 'ok', edicao }), () => setEstadoEdicao({ fase: 'erro' }))
  }, [])
  useEffect(() => { lerBanco() }, [lerBanco])
  const edicao = estadoEdicao.fase === 'ok' ? estadoEdicao.edicao : null
  // Depois do Aplicar: os números novos do ETL e a versão aplicada.
  const atualizado = useCallback(() => { abrir(); lerBanco() }, [abrir, lerBanco])

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
  // Sem resumo: travessão quando os dados chegaram sem nenhum mês, vazio enquanto carregam.
  const vazio = semDados ? '—' : ''
  const valor = (v: number | null, f: (x: number | null) => string = brl) => (resumo ? f(v) : vazio)
  const rotulo = mes ? rotuloMes(mes) : vazio

  let cartao: ReactNode
  if (estado.fase === 'erro') {
    cartao = <FalhaCarga onTentar={abrir} />
  } else if (categoria) {
    cartao = <CartaoDetalhe categoria={categoria} onVoltar={() => setSelecionada(null)} />
  } else {
    cartao = (
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
          <div className="flex items-center gap-4 flex-wrap">
            <div className="text-[18px] font-semibold">Finanças</div>
            <nav className="flex items-center gap-1 bg-cartao rounded-[999px] p-1">
              {TELAS.map(([id, nome]) => (
                <button
                  key={id}
                  aria-pressed={tela === id}
                  onClick={() => setTela(id)}
                  className={`min-h-11 px-[18px] rounded-[999px] text-[15px] flex items-center ${tela === id ? 'bg-ativa text-acento font-semibold' : 'text-secundario font-medium'}`}
                >
                  {nome}
                </button>
              ))}
            </nav>
          </div>
          {tela === 'resumo' && (
            <div className="flex items-center gap-1 bg-cartao rounded-[999px] p-1">
              <button
                className={classeSeta}
                aria-label="Mês anterior"
                disabled={indice <= 0}
                onClick={() => irPara(lista[indice - 1])}
              >
                <Seta d="M15 6l-6 6 6 6" />
              </button>
              <div className="text-[15px] font-medium px-2">{rotulo}</div>
              <button
                className={classeSeta}
                aria-label="Próximo mês"
                disabled={indice < 0 || indice >= lista.length - 1}
                onClick={() => irPara(lista[indice + 1])}
              >
                <Seta d="M9 6l6 6-6 6" />
              </button>
            </div>
          )}
        </header>

        <FaixaPendencia pendente={edicao?.pendente ?? false} onAtualizado={atualizado} />

        {tela === 'resumo' && (
          <div className="flex flex-wrap gap-5 items-stretch">
            <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] p-8 box-border flex flex-col gap-7">
              {cartao}
            </section>

            <ListaCategorias categorias={resumo?.categorias ?? []} ativa={categoria?.id ?? null} onEscolher={escolher} />
          </div>
        )}
        {tela === 'conferencia' && <TelaConferencia aplicada={edicao?.aplicada ?? null} onMudou={lerBanco} />}
        {/* A Conferência relê as tabelas ao abrir; o job extrato também mexe nas do Resumo (abrir). */}
        {tela === 'extratos' && <TelaExtratos onMudou={abrir} />}
        {tela === 'categorias' && edicao && <TelaCategorias edicao={edicao} onMudou={lerBanco} />}
        {tela === 'transacoes' && edicao && (
          <TelaTransacoes transacoes={dados?.transacoes ?? null} edicao={edicao} onMudou={lerBanco} />
        )}
        {(tela === 'categorias' || tela === 'transacoes') && estadoEdicao.fase === 'erro' && (
          <section className="bg-cartao rounded-[36px] p-8"><FalhaCarga onTentar={lerBanco} /></section>
        )}
      </div>
    </div>
  )
}
