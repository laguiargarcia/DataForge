import { useCallback, useEffect, useRef, useState } from 'react'
import { andamento, lerEdicoes, lerTransacoes, pedirAplicar, type Edicoes, type Execucao, type Transacao } from './api'
import { Aplicador, guardaLocal, mensagem, textoAndamento, textoFim, textoSemContato, type Fim } from './aplicar'
import { deslocar, mesDe, mesValido, rotuloMes } from './formato'
import { Linha } from './Linha'

// A tela do esqueleto (df-8x0m.23.13.5), feita para a tela de um iPhone: as transações do mês,
// corrigir a categoria de uma, o aviso de edições não aplicadas e o aplicar com o andamento.

type Carga<T> = { fase: 'carregando' } | { fase: 'erro'; mensagem: string } | { fase: 'ok'; valor: T }

/** O mês do endereço (?mes=AAAA-MM), para recarregar a página no mesmo mês; senão, o mês de hoje. */
function mesInicial(): string {
  const pedido = new URLSearchParams(location.search).get('mes')
  return mesValido(pedido) ? pedido : mesDe(new Date())
}

const Seta = ({ d }: { d: string }) => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
)
const classeSeta = 'w-11 h-11 rounded-[999px] flex items-center justify-center text-secundario'
const classeBotao = 'h-11 px-5 rounded-[999px] bg-acento text-white font-medium shrink-0 disabled:opacity-40'

export function App() {
  const [mes, setMes] = useState(mesInicial)
  const mesAtual = useRef(mes)
  const [transacoes, setTransacoes] = useState<Carga<Transacao[]>>({ fase: 'carregando' })
  const [edicoes, setEdicoes] = useState<Carga<Edicoes>>({ fase: 'carregando' })
  const [aberta, setAberta] = useState<string | null>(null)
  // Do toque em "Aplicar" (ou da página aberta com um aplicar em curso) até as leituras voltarem.
  const [aplicando, setAplicando] = useState(false)
  const [execucao, setExecucao] = useState<Execucao | null>(null)
  const [semContato, setSemContato] = useState<string | null>(null)
  const [falhaAplicar, setFalhaAplicar] = useState<string | null>(null)
  const [aplicador] = useState(() => new Aplicador({ pedirAplicar, andamento }, guardaLocal('ninho.aplicar.run_id'), {
    aoMudar: e => { setExecucao(e); setSemContato(null) },
    aoPerderContato: e => setSemContato(textoSemContato(e)),
  }))

  const carregarTransacoes = useCallback((m: string) =>
    lerTransacoes(m).then(
      valor => { if (mesAtual.current === m) setTransacoes({ fase: 'ok', valor }) },
      e => { if (mesAtual.current === m) setTransacoes({ fase: 'erro', mensagem: mensagem(e) }) },
    ), [])
  const carregarEdicoes = useCallback(() =>
    lerEdicoes().then(valor => setEdicoes({ fase: 'ok', valor }), e => setEdicoes({ fase: 'erro', mensagem: mensagem(e) })), [])

  useEffect(() => {
    mesAtual.current = mes
    history.replaceState(null, '', `?mes=${mes}`)
    setTransacoes({ fase: 'carregando' })
    setAberta(null)
    carregarTransacoes(mes)
  }, [mes, carregarTransacoes])
  useEffect(() => { carregarEdicoes() }, [carregarEdicoes])

  const acompanhar = useCallback((execucaoInteira: Promise<Fim> | null) => {
    if (!execucaoInteira) return   // um aplicar já está em curso: o toque não pede outro
    setAplicando(true)
    setFalhaAplicar(null)
    execucaoInteira
      // A faixa de andamento fica até as duas leituras voltarem, para o aviso não piscar.
      .then(fim => Promise.all([carregarTransacoes(mesAtual.current), carregarEdicoes()]).then(() => fim))
      .then(textoFim, (e: unknown) => `Erro na tela ao acompanhar o aplicar: ${mensagem(e)}`)
      // Com fim ou com erro, a tela sai do "Aplicando" e mostra o aviso, se houver.
      .then(aviso => {
        setAplicando(false)
        setExecucao(null)
        setSemContato(null)
        setFalhaAplicar(aviso)
      })
  }, [carregarTransacoes, carregarEdicoes])
  const iniciarAplicar = () => acompanhar(aplicador.iniciar())
  // A página aberta (ou recarregada) com um aplicar guardado volta a segui-lo em vez de oferecer outro.
  useEffect(() => { acompanhar(aplicador.retomar()) }, [aplicador, acompanhar])

  const valorEdicoes = edicoes.fase === 'ok' ? edicoes.valor : null
  const correcao = (id: string) => valorEdicoes?.correcoes.find(c => c.transacao_id === id)

  return (
    <div className="min-h-dvh bg-fundo font-sans text-[16px] text-texto">
      <header className="sticky top-0 z-10 bg-fundo pt-[max(12px,env(safe-area-inset-top))] pb-2 px-4">
        <div className="max-w-[480px] mx-auto">
          <h1 className="m-0 text-[22px] font-semibold">Transações</h1>
          <nav className="flex items-center justify-between mt-1" aria-label="Mês">
            <button type="button" className={classeSeta} aria-label="Mês anterior" onClick={() => setMes(deslocar(mes, -1))}>
              <Seta d="M15 18l-6-6 6-6" />
            </button>
            <span className="font-medium">{rotuloMes(mes)}</span>
            <button type="button" className={classeSeta} aria-label="Próximo mês" onClick={() => setMes(deslocar(mes, 1))}>
              <Seta d="M9 18l6-6-6-6" />
            </button>
          </nav>
        </div>
      </header>

      <main className="max-w-[480px] mx-auto px-4 pb-[max(24px,env(safe-area-inset-bottom))] flex flex-col gap-3">
        {aplicando && (
          <output className="block rounded-[14px] bg-ativa px-4 py-3">
            {semContato ?? (execucao ? textoAndamento(execucao) : 'Aplicando…')}
          </output>
        )}
        {!aplicando && falhaAplicar && (
          <div role="alert" className="rounded-[14px] bg-alerta-fundo px-4 py-3 flex items-center gap-3">
            <p className="m-0 flex-1 text-alerta break-words min-w-0">{falhaAplicar}</p>
            <button type="button" className={classeBotao} onClick={iniciarAplicar}>Tentar de novo</button>
          </div>
        )}
        {!aplicando && !falhaAplicar && valorEdicoes?.pendente && (
          <div className="rounded-[14px] bg-cartao px-4 py-3 flex items-center gap-3">
            <p className="m-0 flex-1">Há edições não aplicadas</p>
            <button type="button" className={classeBotao} onClick={iniciarAplicar}>Aplicar</button>
          </div>
        )}
        {edicoes.fase === 'erro' && (
          <p role="alert" className="m-0 text-[14px] text-alerta">Não foi possível ler as edições: {edicoes.mensagem}</p>
        )}

        <section className="rounded-[14px] bg-cartao overflow-hidden" aria-label="Transações do mês">
          {transacoes.fase === 'carregando' && <p className="m-0 px-4 py-6 text-secundario">Carregando…</p>}
          {transacoes.fase === 'erro' && (
            <div className="px-4 py-6 flex flex-col gap-3 items-start">
              <p className="m-0">Não foi possível carregar as transações: {transacoes.mensagem}</p>
              <button type="button" className={classeBotao} onClick={() => carregarTransacoes(mes)}>Tentar de novo</button>
            </div>
          )}
          {transacoes.fase === 'ok' && transacoes.valor.length === 0 && (
            <p className="m-0 px-4 py-6 text-secundario">Nenhuma transação em {rotuloMes(mes)}.</p>
          )}
          {transacoes.fase === 'ok' && transacoes.valor.length > 0 && (
            <ul className="m-0 p-0 list-none">
              {transacoes.valor.map(t => (
                <Linha key={t.id} transacao={t} correcao={correcao(t.id)} categorias={valorEdicoes?.categorias ?? []}
                  aberta={aberta === t.id} alternar={() => setAberta(aberta === t.id ? null : t.id)}
                  gravado={() => { setAberta(null); carregarEdicoes() }} />
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
