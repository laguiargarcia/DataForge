import type { Categoria } from '../resumo'

type Props = Readonly<{ categorias: Categoria[]; ativa: string | null; onEscolher: (id: string) => void }>

export function ListaCategorias({ categorias, ativa, onEscolher }: Props) {
  return (
    <section className="flex-[1_1_440px] min-w-0 bg-cartao rounded-[36px] py-6 px-4 box-border flex flex-col gap-1">
      <h2 className="mt-1 mr-0 mb-[10px] ml-4 text-[16px] font-semibold">Para onde foi</h2>
      {categorias.map(c => (
        <button
          key={c.id}
          className={`row w-full box-border min-h-[76px] py-[14px] px-4 rounded-[24px] flex flex-col justify-center gap-[10px] ${c.id === ativa ? 'bg-ativa' : ''}`}
          aria-pressed={c.id === ativa}
          onClick={() => onEscolher(c.id)}
        >
          <span className="flex justify-between gap-4 items-baseline">
            <span className="text-[16px] font-medium">{c.nome}</span>
            <span className="text-[16px] font-semibold whitespace-nowrap">{c.valor}</span>
          </span>
          <span className="block h-2 rounded-[999px] bg-linha overflow-hidden">
            <span className="block h-2 rounded-[999px] bg-acento" style={{ width: c.width }} />
          </span>
        </button>
      ))}
    </section>
  )
}
