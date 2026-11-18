type Props = { onTentar: () => void }

export function FalhaCarga({ onTentar }: Readonly<Props>) {
  return (
    <div className="flex flex-col gap-5 items-start">
      <span className="text-[16px] text-secundario">Não foi possível carregar os dados.</span>
      <button
        onClick={onTentar}
        className="min-h-11 px-[18px] rounded-[999px] bg-faixa text-acento text-[15px] font-semibold flex items-center"
      >
        Tentar de novo
      </button>
    </div>
  )
}
