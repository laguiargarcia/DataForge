type Props = Readonly<{
  sobrou: string
  entrou: string
  saiu: string
  naConta: string
  investido: string
  fatura: string
}>

function Faixa({ rotulo, valor }: Readonly<{ rotulo: string; valor: string }>) {
  return (
    <div className="bg-faixa rounded-[20px] py-[14px] px-[18px] flex justify-between gap-4">
      <span className="text-[15px] text-secundario">{rotulo}</span>
      <span className="text-[16px] font-semibold">{valor}</span>
    </div>
  )
}

export function CartaoResumo({ sobrou, entrou, saiu, naConta, investido, fatura }: Props) {
  return (
    <div className="flex flex-col gap-7 flex-1">
      <div className="flex flex-col gap-3">
        <span className="text-[16px] text-secundario">Sobrou este mês</span>
        <span className="text-[clamp(44px,6vw,68px)] leading-none font-semibold tracking-[-0.03em] text-acento">{sobrou}</span>
        <div className="flex gap-6 flex-wrap text-[16px] text-secundario mt-2">
          <div>Entrou <span className="text-texto font-semibold">{entrou}</span></div>
          <div>Saiu <span className="text-texto font-semibold">{saiu}</span></div>
        </div>
      </div>
      <div className="flex flex-col gap-2 mt-auto">
        <Faixa rotulo="Na conta" valor={naConta} />
        <Faixa rotulo="Investido" valor={investido} />
        <Faixa rotulo="Próxima fatura" valor={fatura} />
      </div>
    </div>
  )
}
