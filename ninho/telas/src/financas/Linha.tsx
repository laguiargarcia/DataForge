import { useState } from 'react'
import { corrigir, desfazer, type Categoria, type Correcao, type Transacao } from './api'
import { brl, diaMes, tipoConta } from './formato'

type Props = Readonly<{
  transacao: Transacao
  correcao: Correcao | undefined
  categorias: Categoria[]
  aberta: boolean
  alternar: () => void
  gravado: () => void
}>

/** O que muda no próximo aplicar, ou null quando a linha já mostra o que está gravado. */
export function falta(t: Transacao, correcao: Correcao | undefined): string | null {
  if (correcao && (correcao.subcategoria !== t.subcategoria || !t.corrigida)) return correcao.subcategoria
  if (!correcao && t.corrigida) return 'volta à categoria das regras'
  return null
}

function porCategoria(categorias: Categoria[]): [string, Categoria[]][] {
  const grupos = new Map<string, Categoria[]>()
  for (const c of categorias) grupos.set(c.categoria, [...(grupos.get(c.categoria) ?? []), c])
  return [...grupos]
}

const mensagem = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function Linha({ transacao: t, correcao, categorias, aberta, alternar, gravado }: Props) {
  const atual = correcao?.subcategoria ?? t.subcategoria ?? ''
  const [escolhida, setEscolhida] = useState(atual)
  const [gravando, setGravando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const mudanca = falta(t, correcao)

  const gravar = (pedido: Promise<unknown>) => {
    setGravando(true)
    setErro(null)
    pedido.then(() => { setGravando(false); gravado() }, e => { setGravando(false); setErro(mensagem(e)) })
  }

  return (
    <li className="border-b border-linha last:border-b-0">
      <button type="button" aria-expanded={aberta} onClick={() => { setEscolhida(atual); setErro(null); alternar() }}
        className="w-full flex items-start gap-3 px-4 py-3 min-h-14">
        <span className="flex-1 min-w-0">
          <span className="block font-medium truncate">{t.description_normalized}</span>
          <span className="block text-[14px] text-secundario">
            {diaMes(t.date)} · {tipoConta(t.account_type)} · <span data-campo="categoria">{t.subcategoria ?? 'Sem categoria'}</span>
            {t.corrigida && <span className="text-acento"> · corrigida</span>}
          </span>
          {mudanca && <span className="block text-[14px] text-acento">→ {mudanca} (falta aplicar)</span>}
        </span>
        <span className={`shrink-0 font-medium tabular-nums ${t.amount_signed > 0 ? 'text-entrada' : ''}`}>
          {brl(t.amount_signed)}
        </span>
      </button>
      {aberta && (
        <div className="px-4 pb-4 flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-[14px] text-secundario">
            <span>Categoria</span>
            <select value={escolhida} onChange={e => setEscolhida(e.target.value)} disabled={gravando}
              className="w-full h-11 rounded-[10px] bg-faixa px-3 text-texto">
              {!categorias.some(c => c.subcategoria === escolhida) && <option value={escolhida} disabled>Escolha</option>}
              {porCategoria(categorias).map(([grupo, itens]) => (
                <optgroup key={grupo} label={grupo}>
                  {itens.map(c => <option key={c.subcategoria} value={c.subcategoria}>{c.subcategoria}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          {erro && <p role="alert" className="m-0 text-[14px] text-alerta">{erro}</p>}
          <div className="flex gap-3">
            <button type="button" disabled={gravando || !escolhida || escolhida === atual}
              onClick={() => gravar(corrigir(t.id, escolhida))}
              className="h-11 px-5 rounded-[999px] bg-marca text-texto font-semibold disabled:opacity-40">
              Salvar
            </button>
            {correcao && (
              <button type="button" disabled={gravando} onClick={() => gravar(desfazer(t.id))}
                className="h-11 px-4 rounded-[999px] text-acento font-medium disabled:opacity-40">
                Desfazer correção
              </button>
            )}
          </div>
        </div>
      )}
    </li>
  )
}
