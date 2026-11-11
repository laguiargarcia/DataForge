import type { Categoria } from '../resumo'

type Props = { categoria: Categoria; onVoltar: () => void }

export function CartaoDetalhe({ categoria, onVoltar }: Props) {
  return (
    <div className="flex flex-col gap-5">
      <button
        onClick={onVoltar}
        className="self-start min-h-11 pl-3 pr-[18px] rounded-[999px] bg-faixa text-acento text-[15px] font-semibold flex items-center gap-[6px]"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
        Resumo do mês
      </button>
      <div className="flex flex-col gap-2">
        <span className="text-[16px] text-secundario">{categoria.nome} · {categoria.grupo} · {categoria.count} · {categoria.share} do que saiu</span>
        <span className="text-[clamp(40px,5vw,56px)] leading-none font-semibold tracking-[-0.03em] text-acento">{categoria.valor}</span>
      </div>
      <div className="flex flex-col">
        {categoria.txs.map(t => (
          <div key={t.id} className="flex justify-between gap-4 py-3 border-t border-linha">
            <div className="flex flex-col gap-[2px] min-w-0">
              <span className="text-[15px]">{t.name}</span>
              <span className="text-[13px] text-secundario">{t.date} · {t.account}</span>
            </div>
            <span className="text-[15px] font-semibold whitespace-nowrap">{t.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
