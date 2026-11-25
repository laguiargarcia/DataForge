import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  acaoDoPresente, adicionar, anotar, apagar, daParte, desanotar, desmarcarPresente, DOS_DOIS, editar, guardarParte,
  lerDesejos, lerParteGuardada, lerPessoas, LIMITE_ANOTACAO, marcarPresente, LIMITE_ANOTACOES, LIMITE_TITULO, parteDe, parteInicial, partes,
  type Anotacao, type Desejo, type Parte, type Pessoa,
} from './api'
import type { Eu } from '../eu'
import { useDados } from '../dados'
import { Logo } from '../Logo'

// A tela do módulo desejos (df-8x0m.23.22.3), para a tela de um iPhone, no molde da Lista: três
// partes no alto (a de cada pessoa e a dos dois), os desejos da parte com as suas anotações (a que o
// servidor diz ser um endereço vira link, aberto em outra aba), e os realizados recolhidos no fim.
// Tocar num desejo abre-o para anotar, apagar uma anotação, mudar o título, mover de parte, marcar
// realizado e apagar; num desejo de outra pessoa, "Vou dar este" (o presente-surpresa,
// df-8x0m.23.23.2). Todos veem e mexem em tudo: a lista é lida ao abrir a tela e de novo cada vez
// que a tela ou a página volta a aparecer (src/dados.ts).

type Dados = { desejos: Desejo[]; pessoas: Pessoa[] }
// Uma função do módulo, estável: a releitura da tela depende dela.
const lerDados = (): Promise<Dados> =>
  Promise.all([lerDesejos(), lerPessoas()]).then(([desejos, pessoas]) => ({ desejos, pessoas }))
type Props = Readonly<{ aberta: boolean; eu: Eu }>
type Alterar = (acao: () => Promise<unknown>, antes?: (desejos: Desejo[]) => Desejo[]) => void

const classeBotao = 'h-11 px-5 rounded-[999px] bg-marca text-texto font-semibold shrink-0 disabled:opacity-40'
const classeCampo = 'flex-1 min-w-0 h-11 px-4 rounded-[999px] font-sans text-[16px] text-texto placeholder:text-secundario'
const classeDiscreto = 'h-11 px-4 rounded-[999px] font-medium shrink-0 disabled:opacity-40'
const classeDesmarcar = `${classeDiscreto} bg-fundo text-texto`

const Xis = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
)
const Seta = ({ aberto }: Readonly<{ aberto: boolean }>) => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"
    className={`shrink-0 text-secundario transition-transform ${aberto ? 'rotate-180' : ''}`}><path d="M6 9l6 6 6-6" /></svg>
)

// O texto vai como texto: o link usa o próprio texto no href, nunca HTML montado a partir dele. O
// servidor só diz link para http e https.
function TextoDaAnotacao({ anotacao }: Readonly<{ anotacao: Anotacao }>) {
  return anotacao.link
    ? <a href={anotacao.texto} target="_blank" rel="noopener noreferrer"
      className="block py-2.5 min-h-11 break-all text-acento underline">{anotacao.texto}</a>
    : <span className="block py-2.5 break-words">{anotacao.texto}</span>
}

const semAnotacao = (desejo: Desejo, anotacao: string) =>
  (d: Desejo): Desejo => (d.id === desejo.id ? { ...d, anotacoes: d.anotacoes.filter(a => a.id !== anotacao) } : d)
const mudado = (desejo: Desejo, mudancas: Partial<Desejo>) =>
  (desejos: Desejo[]) => desejos.map(d => (d.id === desejo.id ? { ...d, ...mudancas } : d))

// A marca e o botão do presente seguem só o que o servidor manda: para o dono `presente` vem sempre
// null, e a tela nunca adivinha (sem mudança otimista: uma suposição poderia piscar a marca errada).
function MarcaDoPresente({ desejo }: Readonly<{ desejo: Desejo }>) {
  if (!desejo.presente) return null
  return (
    <p className="m-0 px-4 pb-2 break-words text-[14px] font-semibold text-entrada">
      {desejo.presente.meu ? 'Você vai dar' : `${desejo.presente.de} vai dar`}
    </p>
  )
}

type CartaoProps = Readonly<{
  desejo: Desejo; eu: string; aberto: boolean; alternar: () => void; fechar: () => void
  todas: readonly Parte[]; ocupado: boolean; alterar: Alterar
}>

function Cartao({ desejo, eu, aberto, alternar, fechar, todas, ocupado, alterar }: CartaoProps) {
  const [nota, setNota] = useState('')
  const [titulo, setTitulo] = useState(desejo.titulo)
  const [confirmando, setConfirmando] = useState(false)
  const campoNota = useRef<HTMLInputElement>(null)
  // O campo do título segue o título do servidor enquanto a pessoa não mexeu nele (a outra pode
  // tê-lo mudado); o que ela digitou e ainda não salvou fica, também quando salvar falha.
  const tituloVisto = useRef(desejo.titulo)
  useEffect(() => {
    setTitulo(atual => (atual.trim() === tituloVisto.current ? desejo.titulo : atual))
    tituloVisto.current = desejo.titulo
  }, [desejo.titulo])
  useEffect(() => { if (aberto) setTitulo(tituloVisto.current) }, [aberto])
  useEffect(() => { if (!aberto) setConfirmando(false) }, [aberto])

  const enviarNota = (evento: FormEvent) => {
    evento.preventDefault()
    const texto = nota.trim()
    if (!texto) return
    campoNota.current?.focus()
    setNota('')
    alterar(() => anotar(desejo.id, texto).catch((e: unknown) => {
      setNota(atual => atual || texto)
      throw e
    }))
  }
  const novoTitulo = titulo.trim()
  const salvarTitulo = (evento: FormEvent) => {
    evento.preventDefault()
    if (!novoTitulo || novoTitulo === desejo.titulo) return
    // Sem mostrar antes da resposta: recusado, o título do cartão não muda e o campo guarda o texto.
    alterar(() => editar(desejo.id, { titulo: novoTitulo }))
  }
  const mover = (chave: string) => {
    const de_quem = chave === DOS_DOIS ? null : chave
    fechar()
    alterar(() => editar(desejo.id, { de_quem }), mudado(desejo, { de_quem }))
  }
  const realizar = () => {
    fechar()
    alterar(() => editar(desejo.id, { realizado: !desejo.realizado }), mudado(desejo, { realizado: !desejo.realizado }))
  }
  const cheio = desejo.anotacoes.length >= LIMITE_ANOTACOES
  const presente = acaoDoPresente(desejo, eu)

  return (
    <li className="border-b border-linha last:border-b-0">
      <button type="button" aria-expanded={aberto} onClick={alternar}
        className="w-full min-h-14 px-4 py-2 flex items-center gap-3">
        <span className={`flex-1 min-w-0 break-words font-semibold ${desejo.realizado ? 'line-through text-secundario' : ''}`}>
          {desejo.titulo}
        </span>
        <Seta aberto={aberto} />
      </button>
      <MarcaDoPresente desejo={desejo} />
      {desejo.anotacoes.length > 0 && (
        <ul aria-label={`Anotações de ${desejo.titulo}`} className="m-0 px-4 pb-2 list-none flex flex-col">
          {desejo.anotacoes.map(a => (
            <li key={a.id} className="flex items-center gap-1 min-w-0">
              <div className="flex-1 min-w-0"><TextoDaAnotacao anotacao={a} /></div>
              {aberto && (
                <button type="button" aria-label={`Apagar a anotação ${a.texto}`} disabled={ocupado}
                  onClick={() => alterar(() => desanotar(desejo.id, a.id), ds => ds.map(semAnotacao(desejo, a.id)))}
                  className="w-11 h-11 shrink-0 rounded-[999px] flex items-center justify-center text-secundario disabled:opacity-40">
                  <Xis />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {aberto && (
        <div className="px-4 pb-4 pt-1 flex flex-col gap-3">
          <form className="flex gap-2" onSubmit={enviarNota}>
            <input ref={campoNota} type="text" aria-label="Nova anotação" value={nota} maxLength={LIMITE_ANOTACAO}
              placeholder={cheio ? `No máximo ${LIMITE_ANOTACOES} anotações` : 'Anotação'} disabled={cheio}
              enterKeyHint="done" autoComplete="off" onChange={e => setNota(e.target.value)} className={`${classeCampo} bg-fundo`} />
            <button type="submit" className={classeBotao} disabled={cheio || !nota.trim()}>Anotar</button>
          </form>
          <form className="flex gap-2" onSubmit={salvarTitulo}>
            <input type="text" aria-label="Título" value={titulo} maxLength={LIMITE_TITULO} enterKeyHint="done"
              autoComplete="off" onChange={e => setTitulo(e.target.value)} className={`${classeCampo} bg-fundo`} />
            <button type="submit" className={classeBotao} disabled={ocupado || !novoTitulo || novoTitulo === desejo.titulo}>
              Salvar
            </button>
          </form>
          <label className="flex items-center gap-3">
            <span className="shrink-0 text-secundario">Mover para</span>
            <select value={parteDe(desejo)} disabled={ocupado} onChange={e => mover(e.target.value)}
              className="flex-1 min-w-0 h-11 px-4 rounded-[999px] bg-fundo text-texto disabled:opacity-40">
              {todas.map(p => <option key={p.chave} value={p.chave}>{p.rotulo}</option>)}
            </select>
          </label>
          {presente && (
            <button type="button" disabled={ocupado}
              onClick={() => alterar(() => (presente === 'marcar' ? marcarPresente : desmarcarPresente)(desejo.id))}
              className={`${presente === 'marcar' ? classeBotao : classeDesmarcar} self-start`}>
              {presente === 'marcar' ? 'Vou dar este' : 'Não vou mais dar'}
            </button>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" disabled={ocupado} onClick={realizar} className={classeBotao}>
              {desejo.realizado ? 'Desmarcar realizado' : 'Realizado'}
            </button>
            {!confirmando && (
              <button type="button" disabled={ocupado} onClick={() => setConfirmando(true)}
                className={`${classeDiscreto} text-alerta`}>
                Apagar desejo
              </button>
            )}
          </div>
          {/* Apagar leva o desejo e as anotações de vez, também os da outra pessoa: só depois de confirmar. */}
          {confirmando && (
            <fieldset aria-label="Confirmar apagar" className="m-0 p-0 border-0 min-w-0">
              <div className="rounded-[20px] bg-fundo px-4 py-3 flex flex-wrap items-center justify-end gap-2">
                <p className="m-0 basis-full min-w-0 break-words">Apagar “{desejo.titulo}”?</p>
                <button type="button" onClick={() => setConfirmando(false)} className={`${classeDiscreto} text-secundario`}>
                  Cancelar
                </button>
                <button type="button" disabled={ocupado} className={classeBotao}
                  onClick={() => { fechar(); alterar(() => apagar(desejo.id), ds => ds.filter(d => d.id !== desejo.id)) }}>
                  Apagar
                </button>
              </div>
            </fieldset>
          )}
        </div>
      )}
    </li>
  )
}

export function Desejos({ aberta, eu }: Props) {
  const { carga, ocupado, falha, alterar: alterarDados, tentar } = useDados(aberta, lerDados, 'os desejos')
  const [escolhida, setEscolhida] = useState(lerParteGuardada)
  const [aberto, setAberto] = useState<string | null>(null)
  const [texto, setTexto] = useState('')
  const campo = useRef<HTMLInputElement>(null)
  // As mudanças otimistas dos cartões mexem só nos desejos.
  const alterar = useCallback<Alterar>((acao, antes) =>
    alterarDados(acao, antes && (v => ({ ...v, desejos: antes(v.desejos) }))), [alterarDados])

  const dados = carga.fase === 'ok' ? carga.valor : null
  const todas = partes(dados?.pessoas ?? [])
  const parte = parteInicial(escolhida, todas, eu.id)
  const { abertos, realizados } = daParte(dados?.desejos ?? [], parte)

  const escolher = (chave: string) => {
    setEscolhida(chave)
    guardarParte(chave)
    setAberto(null)
  }

  const enviar = (evento: FormEvent) => {
    evento.preventDefault()
    const novo = texto.trim()
    if (!novo || !dados) return
    campo.current?.focus()
    setTexto('')
    alterar(() => adicionar(novo, parte === DOS_DOIS ? null : parte).catch((e: unknown) => {
      setTexto(atual => atual || novo)
      throw e
    }))
  }

  // O cartão aberto vale para o desejo onde ele estava: realizado pela outra pessoa, ele vai para o
  // arquivo fechado, não aberto lá dentro.
  const chave = (d: Desejo) => `${d.id}:${d.realizado}`
  const cartao = (d: Desejo) => (
    <Cartao key={d.id} desejo={d} eu={eu.id} aberto={aberto === chave(d)} todas={todas} ocupado={ocupado} alterar={alterar}
      alternar={() => setAberto(a => (a === chave(d) ? null : chave(d)))} fechar={() => setAberto(null)} />
  )

  return (
    <div className="min-h-dvh bg-fundo font-sans text-[16px] text-texto">
      <header className="sticky top-0 z-10 bg-fundo pt-[max(12px,env(safe-area-inset-top))] pb-3 px-4">
        <div className="max-w-[480px] mx-auto flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <h1 className="m-0 font-titulo text-[30px] leading-tight font-bold text-acento">Desejos</h1>
            <Logo />
          </div>
          {dados && (
            <fieldset aria-label="De quem" className="m-0 p-0 border-0 min-w-0">
              <div className="flex flex-wrap gap-2">
                {todas.map(p => (
                  <button key={p.chave} type="button" aria-pressed={p.chave === parte} onClick={() => escolher(p.chave)}
                    className={`h-11 px-4 rounded-[999px] font-semibold max-w-full truncate ${p.chave === parte ? 'bg-marca text-texto' : 'bg-cartao text-secundario'}`}>
                    {p.rotulo}
                  </button>
                ))}
              </div>
            </fieldset>
          )}
          <form className="flex gap-2" onSubmit={enviar}>
            <input ref={campo} type="text" aria-label="Novo desejo" placeholder="Novo desejo" value={texto} maxLength={LIMITE_TITULO}
              enterKeyHint="done" autoComplete="off" onChange={e => setTexto(e.target.value)}
              className={`${classeCampo} bg-cartao`} />
            <button type="submit" className={classeBotao} disabled={!dados || !texto.trim()}>Adicionar</button>
          </form>
        </div>
      </header>

      <main className="max-w-[480px] mx-auto px-4 pb-[max(24px,env(safe-area-inset-bottom))] flex flex-col gap-3">
        {falha && <p role="alert" className="m-0 rounded-[20px] bg-alerta-fundo px-4 py-3 text-alerta break-words">{falha}</p>}

        <section className="flex flex-col gap-3" aria-label="Desejos da parte">
          {carga.fase === 'carregando' && <p className="m-0 rounded-[20px] bg-cartao px-4 py-6 text-secundario">Carregando…</p>}
          {carga.fase === 'erro' && (
            <div className="rounded-[20px] bg-cartao px-4 py-6 flex flex-col gap-3 items-start">
              <p className="m-0 break-words">Não foi possível carregar os desejos: {carga.mensagem}</p>
              <button type="button" className={classeBotao} onClick={tentar}>
                Tentar de novo
              </button>
            </div>
          )}
          {dados && abertos.length === 0 && (
            <p className="m-0 rounded-[20px] bg-cartao px-4 py-6 text-secundario">
              {realizados.length === 0 ? 'Nenhum desejo aqui ainda.' : 'Nenhum desejo em aberto.'}
            </p>
          )}
          {abertos.length > 0 && (
            <ul aria-label="Desejos" className="m-0 p-0 list-none rounded-[20px] bg-cartao overflow-hidden">
              {abertos.map(cartao)}
            </ul>
          )}
          {/* Os realizados ficam em arquivo, recolhidos no fim da parte: dá para desmarcar ou apagar. */}
          {realizados.length > 0 && (
            <details key={parte} className="flex flex-col gap-3">
              <summary className="cursor-pointer py-3 font-semibold text-secundario">Realizados ({realizados.length})</summary>
              <ul aria-label="Realizados" className="m-0 p-0 list-none rounded-[20px] bg-cartao overflow-hidden">
                {realizados.map(cartao)}
              </ul>
            </details>
          )}
        </section>
      </main>
    </div>
  )
}
