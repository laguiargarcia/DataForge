import { useMemo, useState } from 'react'
import { corrigir, desfazerCorrecao, type Edicao } from '../edicao'
import { brl, type TxClassificada } from '../resumo'
import {
  LIMITE_LINHAS, SEM_CATEGORIA, categoriasDe, correcoesPorId, dataCurta, estadoCorrecao, filtrar, mesesDe, orfas,
  rotuloAnomes, textoConfirmacao, textoContagem, textoEstado, type Filtro,
} from '../transacoes'

type Props = { transacoes: TxClassificada[] | null; edicao: Edicao; onMudou: () => void }

const botao = 'min-h-11 px-4 rounded-[999px] text-[15px] font-semibold flex items-center'
const campo = 'min-h-11 box-border rounded-[14px] bg-faixa px-3 text-[15px] text-texto'

// Lista do curated com busca e filtros. Corrigir grava a correção no banco na hora; a categoria da
// linha só muda depois do Aplicar, e até lá a linha diz o que falta.
export function TelaTransacoes({ transacoes, edicao, onMudou }: Readonly<Props>) {
  const [filtro, setFiltro] = useState<Filtro>({ busca: '', mes: null, categoria: null })
  const [editando, setEditando] = useState<{ id: string; subcategoria: string; erro: string | null } | null>(null)
  const [erro, setErro] = useState<{ id: string; mensagem: string } | null>(null)

  const txs = useMemo(() => transacoes ?? [], [transacoes])
  const lista = useMemo(() => filtrar(txs, filtro), [txs, filtro])
  const correcoes = useMemo(() => correcoesPorId(edicao.correcoes), [edicao.correcoes])
  const subcategorias = edicao.categorias.map(c => c.subcategoria)
  const semDono = orfas(edicao.correcoes)

  const falhar = (id: string) => (e: Error) => setErro({ id, mensagem: e.message })
  const salvar = (t: TxClassificada) => {
    if (!editando?.subcategoria) return
    const confirmacao = textoConfirmacao(t, editando.subcategoria)
    if (confirmacao && !globalThis.confirm(confirmacao)) return
    corrigir(t.id, editando.subcategoria).then(
      () => { setEditando(null); onMudou() },
      (e: Error) => setEditando(atual => (atual ? { ...atual, erro: e.message } : atual)),
    )
  }
  const desfazer = (id: string) => {
    setErro(null)
    desfazerCorrecao(id).then(onMudou, falhar(id))
  }

  return (
    <div className="flex flex-col gap-5">
      {semDono.length > 0 && (
        <section className="bg-alerta-fundo rounded-[36px] py-6 px-8 box-border flex flex-col gap-3">
          <h2 className="m-0 text-[16px] font-semibold text-alerta">Correções de transações não encontradas</h2>
          <p className="m-0 text-[14px] text-secundario">
            Estas transações não estão mais nos dados (o banco pode tê-las trocado ou anulado). A correção não faz nada; apague-a.
          </p>
          {semDono.map(c => (
            <div key={c.transacao_id} className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[15px]">Transação não encontrada · {c.transacao_id} → {c.subcategoria}</span>
              <button className={`${botao} bg-cartao text-alerta`} onClick={() => desfazer(c.transacao_id)}>Apagar</button>
              {erro?.id === c.transacao_id && <span role="alert" className="w-full text-[14px] text-alerta">{erro.mensagem}</span>}
            </div>
          ))}
        </section>
      )}

      <section className="bg-cartao rounded-[36px] py-6 px-4 box-border flex flex-col gap-3">
        <div className="flex flex-wrap gap-3 mx-4">
          <input
            className={`${campo} flex-[2_1_240px]`}
            placeholder="Buscar na descrição"
            aria-label="Buscar na descrição"
            value={filtro.busca}
            onChange={e => setFiltro({ ...filtro, busca: e.target.value })}
          />
          <select
            className={`${campo} flex-[1_1_160px]`}
            aria-label="Mês"
            value={filtro.mes ?? ''}
            onChange={e => setFiltro({ ...filtro, mes: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">Todos os meses</option>
            {mesesDe(txs).map(m => <option key={m} value={m}>{rotuloAnomes(m)}</option>)}
          </select>
          <select
            className={`${campo} flex-[1_1_160px]`}
            aria-label="Categoria"
            value={filtro.categoria ?? ''}
            onChange={e => setFiltro({ ...filtro, categoria: e.target.value || null })}
          >
            <option value="">Todas as categorias</option>
            {categoriasDe(txs).map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <p className="m-0 mx-4 text-[14px] text-secundario">
          {textoContagem(lista.length)}
        </p>

        {lista.slice(0, LIMITE_LINHAS).map(t => {
          const estado = textoEstado(estadoCorrecao(t, correcoes))
          const semCategoria = t.subcategoria === SEM_CATEGORIA
          const aberta = editando?.id === t.id
          return (
            <div key={t.id} className={`py-3 px-4 rounded-[24px] flex flex-col gap-2 ${aberta ? 'bg-ativa' : 'row'}`}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="w-[92px] text-[14px] text-secundario">{dataCurta(t.date)}</span>
                <span className="flex-[2_1_220px] text-[15px]">{t.description_normalized ?? '—'}</span>
                <span className={`flex-[1_1_160px] text-[15px] ${semCategoria ? 'text-alerta font-semibold' : ''}`}>
                  {t.categoria}
                  {t.subcategoria !== t.categoria && <span className="block text-[13px] text-secundario">{t.subcategoria}</span>}
                </span>
                <span className="w-[120px] text-right text-[15px] font-semibold whitespace-nowrap">{brl(t.amount_signed)}</span>
                <div className="flex gap-2">
                  {!aberta && (
                    <button
                      className={`${botao} text-acento`}
                      disabled={editando !== null}
                      onClick={() => { setErro(null); setEditando({ id: t.id, subcategoria: correcoes.get(t.id) ?? '', erro: null }) }}
                    >
                      Corrigir
                    </button>
                  )}
                  {!aberta && correcoes.has(t.id) && (
                    <button className={`${botao} text-secundario`} disabled={editando !== null} onClick={() => desfazer(t.id)}>Desfazer</button>
                  )}
                </div>
              </div>
              {estado && <span className="text-[13px] font-medium text-acento">{estado}</span>}
              {erro?.id === t.id && <span role="alert" className="text-[14px] text-alerta">{erro.mensagem}</span>}
              {aberta && (
                <div className="flex flex-wrap items-center gap-3">
                  <select
                    className={`${campo} flex-[1_1_220px]`}
                    aria-label="Nova subcategoria"
                    value={editando.subcategoria}
                    onChange={e => setEditando({ ...editando, subcategoria: e.target.value })}
                  >
                    <option value="">(escolha a subcategoria)</option>
                    {subcategorias.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                  <button className={`${botao} bg-acento text-cartao`} disabled={!editando.subcategoria} onClick={() => salvar(t)}>Salvar</button>
                  <button className={`${botao} text-secundario`} onClick={() => setEditando(null)}>Cancelar</button>
                  {editando.erro && <span role="alert" className="w-full text-[14px] text-alerta">{editando.erro}</span>}
                </div>
              )}
            </div>
          )
        })}
      </section>
    </div>
  )
}
