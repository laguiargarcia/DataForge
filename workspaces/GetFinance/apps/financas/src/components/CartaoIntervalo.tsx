import { useState } from 'react'
import type { Intervalo } from '../conferencia'
import { desfazerExplicacao, explicar } from '../edicao'
import { Faixa } from './CartaoResumo'
import { ListaSuspeitos } from './ListaSuspeitos'
import { SeloEstado } from './SeloEstado'

// onMudou: o banco de edição mudou (a faixa de pendência pede o Aplicar, que recalcula a conferência).
type Props = { intervalo: Intervalo; onVoltar: () => void; onMudou: () => void }

const botao = 'min-h-11 px-4 rounded-[999px] text-[15px] font-semibold flex items-center'
const campo = 'min-h-11 box-border rounded-[14px] bg-faixa px-3 text-[15px] text-texto'
const SALVO = 'Salvo. Use o Aplicar para atualizar a conferência.'

// df-y26v: o dono marca uma divergência que ele sabe explicar; a marca vale enquanto a diferença for a mesma.
function Explicacao({ intervalo, onMudou }: Readonly<Omit<Props, 'onVoltar'>>) {
  const [motivo, setMotivo] = useState<string | null>(null) // null: formulário fechado
  const [aviso, setAviso] = useState<{ erro: boolean; texto: string } | null>(null)

  const feito = () => { setMotivo(null); setAviso({ erro: false, texto: SALVO }); onMudou() }
  const falhou = (e: Error) => setAviso({ erro: true, texto: e.message })
  const salvar = () => explicar(intervalo.conta, intervalo.ateIso, motivo ?? '').then(feito, falhou)
  const desfazer = () => desfazerExplicacao(intervalo.conta, intervalo.ateIso).then(feito, falhou)

  let corpo = null
  if (intervalo.estado === 'explicado') {
    corpo = (
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex-[1_1_220px] text-[15px]">{intervalo.explicacao}</span>
        <button className={`${botao} text-secundario`} onClick={desfazer}>Desfazer</button>
      </div>
    )
  } else if (intervalo.podeExplicar && motivo === null) {
    corpo = (
      <button className={`${botao} self-start bg-faixa text-acento`} onClick={() => { setAviso(null); setMotivo('') }}>
        Marcar como explicado
      </button>
    )
  } else if (intervalo.podeExplicar) {
    corpo = (
      <div className="flex flex-wrap items-center gap-3">
        <input
          className={`${campo} flex-[1_1_220px]`}
          aria-label="Motivo da divergência"
          placeholder="Por que esta diferença está explicada"
          value={motivo ?? ''}
          onChange={e => setMotivo(e.target.value)}
        />
        <button className={`${botao} bg-acento text-cartao`} disabled={!motivo?.trim()} onClick={salvar}>Salvar</button>
        <button className={`${botao} text-secundario`} onClick={() => setMotivo(null)}>Cancelar</button>
      </div>
    )
  }
  if (corpo === null && aviso === null) return null

  return (
    <div className="flex flex-col gap-2">
      <h3 className="m-0 text-[16px] font-semibold">Divergência explicada</h3>
      {corpo}
      {aviso && (
        <span role={aviso.erro ? 'alert' : 'status'} className={`text-[14px] ${aviso.erro ? 'text-alerta' : 'text-acento'}`}>
          {aviso.texto}
        </span>
      )}
    </div>
  )
}

export function CartaoIntervalo({ intervalo, onVoltar, onMudou }: Readonly<Props>) {
  return (
    <div className="flex flex-col gap-5">
      <button
        onClick={onVoltar}
        className="self-start min-h-11 pl-3 pr-[18px] rounded-[999px] bg-faixa text-acento text-[15px] font-semibold flex items-center gap-[6px]"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
        Conferência
      </button>
      <div className="flex flex-col gap-2">
        <span className="text-[16px] text-secundario">{intervalo.conta} · {intervalo.de} → {intervalo.ate}</span>
        <span className={`text-[clamp(40px,5vw,56px)] leading-none font-semibold tracking-[-0.03em] ${intervalo.fecha ? 'text-acento' : 'text-alerta'}`}>{intervalo.diferenca}</span>
        <span><SeloEstado intervalo={intervalo} /></span>
      </div>
      <div className="flex flex-col gap-2">
        <Faixa rotulo="Variação do saldo" valor={intervalo.variacao} />
        <Faixa rotulo={`Soma dos lançamentos (${intervalo.lancamentos})`} valor={intervalo.soma} />
      </div>
      <Explicacao key={intervalo.id} intervalo={intervalo} onMudou={onMudou} />
      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-[16px] font-semibold">Suspeitos</h3>
        <ListaSuspeitos suspeitos={intervalo.suspeitos} vazio="Nenhum lançamento suspeito neste intervalo." />
      </div>
    </div>
  )
}
