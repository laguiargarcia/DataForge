import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { adicionar, apagar, lerItens, LIMITE, limparComprados, marcar, separar, type Item } from './api'

// A tela do módulo lista (df-8x0m.23.20.3), para a tela de um iPhone: adicionar um item, marcar e
// desmarcar comprado, apagar um e limpar os comprados. A lista é dos dois: ela é lida ao abrir a tela
// e de novo cada vez que a tela ou a página volta a aparecer, porque a outra pessoa pode ter mexido.
// Precisa de rede: sem guarda local nem fila (df-8x0m.23.8).

type Carga = { fase: 'carregando' } | { fase: 'erro'; mensagem: string } | { fase: 'ok'; valor: Item[] }
type Props = Readonly<{ aberta: boolean }>

const mensagem = (e: unknown) => (e instanceof Error ? e.message : String(e))
const classeBotao = 'h-11 px-5 rounded-[999px] bg-acento text-white font-medium shrink-0 disabled:opacity-40'

const Xis = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
)

type Alterar = (acao: () => Promise<unknown>, antes?: (itens: Item[]) => Item[]) => void

function Linha({ item, ocupado, alterar }: Readonly<{ item: Item; ocupado: boolean; alterar: Alterar }>) {
  return (
    <li className="flex items-center border-b border-linha last:border-b-0">
      <label className="flex-1 min-w-0 flex items-center gap-3 pl-4 py-2 min-h-14 cursor-pointer">
        <input type="checkbox" aria-label={item.texto} checked={item.comprado} disabled={ocupado}
          onChange={() => alterar(() => marcar(item.id, !item.comprado),
            itens => itens.map(i => (i.id === item.id ? { ...i, comprado: !item.comprado } : i)))}
          className="w-6 h-6 shrink-0 accent-acento" />
        <span className="min-w-0">
          <span className={`block break-words ${item.comprado ? 'line-through text-secundario' : ''}`}>{item.texto}</span>
          {item.adicionado_por && <span className="block text-[13px] text-secundario">{item.adicionado_por}</span>}
        </span>
      </label>
      <button type="button" aria-label={`Apagar ${item.texto}`} disabled={ocupado}
        onClick={() => alterar(() => apagar(item.id), itens => itens.filter(i => i.id !== item.id))}
        className="w-11 h-11 mr-1 shrink-0 rounded-[999px] flex items-center justify-center text-secundario disabled:opacity-40">
        <Xis />
      </button>
    </li>
  )
}

export function Lista({ aberta }: Props) {
  const [carga, setCarga] = useState<Carga>({ fase: 'carregando' })
  const [texto, setTexto] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [falha, setFalha] = useState<string | null>(null)
  // Só a leitura mais recente vale: uma resposta atrasada não apaga outra mais nova.
  const ultima = useRef(0)

  const reler = useCallback(() => {
    const esta = ++ultima.current
    return lerItens().then(
      valor => { if (ultima.current === esta) setCarga({ fase: 'ok', valor }) },
      e => { if (ultima.current === esta) setCarga({ fase: 'erro', mensagem: mensagem(e) }) },
    )
  }, [])

  // A casca mantém a tela montada e escondida: ler só quando ela está aberta, e reler quando a
  // página volta a ficar visível (outra aba, o iPhone desbloqueado).
  useEffect(() => {
    if (!aberta) return
    reler()
    const aoVoltar = () => { if (document.visibilityState === 'visible') reler() }
    document.addEventListener('visibilitychange', aoVoltar)
    return () => document.removeEventListener('visibilitychange', aoVoltar)
  }, [aberta, reler])

  // Cada mudança vai ao servidor e depois relê a lista inteira, com a ordem do servidor e o que a
  // outra pessoa tenha mudado; se falhar, relê também (o item pode ter sido apagado do outro lado).
  // Marcar e apagar já aparecem no toque (`antes`); a releitura confirma ou desfaz.
  const alterar = useCallback<Alterar>((acao, antes) => {
    setOcupado(true)
    setFalha(null)
    if (antes) setCarga(c => (c.fase === 'ok' ? { fase: 'ok', valor: antes(c.valor) } : c))
    acao()
      .catch((e: unknown) => setFalha(mensagem(e)))
      .then(reler)
      .finally(() => setOcupado(false))
  }, [reler])

  const enviar = (evento: FormEvent) => {
    evento.preventDefault()
    const novo = texto.trim()
    if (!novo) return
    // O campo limpa na hora (o foco fica nele) para o próximo item; se o servidor recusar, o texto volta.
    setTexto('')
    alterar(() => adicionar(novo).catch((e: unknown) => {
      setTexto(atual => atual || novo)
      throw e
    }))
  }

  const { faltam, comprados } = separar(carga.fase === 'ok' ? carga.valor : [])

  return (
    <div className="min-h-dvh bg-fundo font-sans text-[16px] text-texto">
      <header className="sticky top-0 z-10 bg-fundo pt-[max(12px,env(safe-area-inset-top))] pb-3 px-4">
        <div className="max-w-[480px] mx-auto flex flex-col gap-2">
          <h1 className="m-0 text-[22px] font-semibold">Lista</h1>
          <form className="flex gap-2" onSubmit={enviar}>
            <input type="text" aria-label="Novo item" placeholder="Novo item" value={texto} maxLength={LIMITE}
              enterKeyHint="done" autoComplete="off" onChange={e => setTexto(e.target.value)}
              className="flex-1 min-w-0 h-11 px-4 rounded-[999px] bg-cartao font-sans text-[16px] text-texto" />
            <button type="submit" className={classeBotao} disabled={!texto.trim()}>Adicionar</button>
          </form>
        </div>
      </header>

      <main className="max-w-[480px] mx-auto px-4 pb-[max(24px,env(safe-area-inset-bottom))] flex flex-col gap-3">
        {falha && <p role="alert" className="m-0 rounded-[14px] bg-alerta-fundo px-4 py-3 text-alerta break-words">{falha}</p>}

        <section className="flex flex-col gap-3" aria-label="Itens da lista">
          {carga.fase === 'carregando' && <p className="m-0 rounded-[14px] bg-cartao px-4 py-6 text-secundario">Carregando…</p>}
          {carga.fase === 'erro' && (
            <div className="rounded-[14px] bg-cartao px-4 py-6 flex flex-col gap-3 items-start">
              <p className="m-0 break-words">Não foi possível carregar a lista: {carga.mensagem}</p>
              <button type="button" className={classeBotao} onClick={() => { setCarga({ fase: 'carregando' }); reler() }}>
                Tentar de novo
              </button>
            </div>
          )}
          {carga.fase === 'ok' && carga.valor.length === 0 && (
            <p className="m-0 rounded-[14px] bg-cartao px-4 py-6 text-secundario">A lista está vazia.</p>
          )}
          {faltam.length > 0 && (
            <ul aria-label="A comprar" className="m-0 p-0 list-none rounded-[14px] bg-cartao overflow-hidden">
              {faltam.map(i => <Linha key={i.id} item={i} ocupado={ocupado} alterar={alterar} />)}
            </ul>
          )}
          {comprados.length > 0 && (
            <>
              <div className="flex items-center justify-between gap-3 pt-2">
                <h2 className="m-0 text-[16px] font-semibold text-secundario">Comprados</h2>
                <button type="button" disabled={ocupado} onClick={() => alterar(limparComprados)}
                  className="h-11 px-4 rounded-[999px] text-acento font-medium disabled:opacity-40">
                  Limpar comprados
                </button>
              </div>
              <ul aria-label="Comprados" className="m-0 p-0 list-none rounded-[14px] bg-cartao overflow-hidden">
                {comprados.map(i => <Linha key={i.id} item={i} ocupado={ocupado} alterar={alterar} />)}
              </ul>
            </>
          )}
        </section>
      </main>
    </div>
  )
}
