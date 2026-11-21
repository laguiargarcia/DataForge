import { useState } from 'react'

export type Coluna<T> = { campo: keyof T & string; titulo: string; opcoes?: string[] }
export type Nota = { resumo: string; aviso: string | null }

type Props<T extends Record<string, string>> = {
  titulo: string
  colunas: Coluna<T>[]
  linhas: T[]
  chave: (linha: T) => string
  nova: T
  /** Grava; devolve uma nota para mostrar embaixo da linha (o efeito de uma regra), ou null. */
  onSalvar: (antiga: string | null, valores: T) => Promise<Nota | null>
  onExcluir: (chave: string) => Promise<void>
}

type Edicao<T> = { antiga: string | null; valores: T; erro: string | null; gravando: boolean }

const botao = 'min-h-11 px-4 rounded-[999px] text-[15px] font-semibold flex items-center'
const campo = 'min-h-11 w-full box-border rounded-[14px] bg-faixa px-3 text-[15px] text-texto'

// Uma linha em edição por vez. Salvar grava direto no banco (sem rascunho); cancelar só descarta o
// que foi digitado naquela linha. O erro do backend aparece na própria linha.
export function TabelaEditavel<T extends Record<string, string>>(
  { titulo, colunas, linhas, chave, nova, onSalvar, onExcluir }: Readonly<Props<T>>,
) {
  const [edicao, setEdicao] = useState<Edicao<T> | null>(null)
  const [notas, setNotas] = useState<Record<string, Nota>>({})
  const [erroExcluir, setErroExcluir] = useState<{ chave: string; mensagem: string } | null>(null)

  const editar = (antiga: string | null, valores: T) => {
    setErroExcluir(null)
    setEdicao({ antiga, valores, erro: null, gravando: false })
  }
  const mudar = (c: keyof T & string, valor: string) =>
    setEdicao(e => (e ? { ...e, valores: { ...e.valores, [c]: valor } } : e))

  const salvar = () => {
    if (!edicao) return
    setEdicao({ ...edicao, gravando: true, erro: null })
    onSalvar(edicao.antiga, edicao.valores).then(
      nota => {
        const nome = chave(edicao.valores)
        setNotas(n => {
          const resto = { ...n }
          if (edicao.antiga !== null) delete resto[edicao.antiga]
          delete resto[nome]
          return nota ? { ...resto, [nome]: nota } : resto
        })
        setEdicao(null)
      },
      (e: Error) => setEdicao(atual => (atual ? { ...atual, gravando: false, erro: e.message } : atual)),
    )
  }

  const excluir = (k: string) => {
    if (!globalThis.confirm(`Excluir «${k}»?`)) return
    setErroExcluir(null)
    onExcluir(k).catch((e: Error) => setErroExcluir({ chave: k, mensagem: e.message }))
  }

  const formulario = (e: Edicao<T>) => (
    <div className="flex flex-col gap-2 py-3 px-4 rounded-[24px] bg-ativa">
      <div className="flex flex-wrap gap-3 items-end">
        {colunas.map(c => (
          <label key={c.campo} className="flex-[1_1_180px] flex flex-col gap-1 text-[13px] text-secundario">
            {c.titulo}
            {c.opcoes ? (
              <select className={campo} value={e.valores[c.campo]} onChange={ev => mudar(c.campo, ev.target.value)}>
                {!c.opcoes.includes(e.valores[c.campo]) && <option value={e.valores[c.campo]}>(escolha)</option>}
                {c.opcoes.map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : (
              <input className={campo} value={e.valores[c.campo]} onChange={ev => mudar(c.campo, ev.target.value)} />
            )}
          </label>
        ))}
        <div className="flex gap-2">
          <button className={`${botao} bg-acento text-cartao`} disabled={e.gravando} onClick={salvar}>Salvar</button>
          <button className={`${botao} text-secundario`} disabled={e.gravando} onClick={() => setEdicao(null)}>Cancelar</button>
        </div>
      </div>
      {e.erro && <span role="alert" className="text-[14px] text-alerta">{e.erro}</span>}
    </div>
  )

  return (
    <section className="bg-cartao rounded-[36px] py-6 px-4 box-border flex flex-col gap-1">
      <div className="flex items-center justify-between gap-4 mt-1 mb-[10px] mx-4">
        <h2 className="m-0 text-[16px] font-semibold">{titulo}</h2>
        <button className={`${botao} bg-faixa text-acento`} disabled={edicao !== null} onClick={() => editar(null, nova)}>
          Adicionar
        </button>
      </div>
      {edicao?.antiga === null && formulario(edicao)}
      {linhas.map(linha => {
        const k = chave(linha)
        if (edicao?.antiga === k) return <div key={k}>{formulario(edicao)}</div>
        const nota = notas[k]
        return (
          <div key={k} className="row py-3 px-4 rounded-[24px] flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-3">
              {colunas.map(c => (
                <span key={c.campo} className="flex-[1_1_180px] text-[15px]">
                  <span className="sr-only">{c.titulo}: </span>{linha[c.campo]}
                </span>
              ))}
              <div className="flex gap-2">
                <button className={`${botao} text-acento`} disabled={edicao !== null} onClick={() => editar(k, linha)}>Editar</button>
                <button className={`${botao} text-secundario`} disabled={edicao !== null} onClick={() => excluir(k)}>Excluir</button>
              </div>
            </div>
            {nota && <span className="text-[14px] text-secundario">{nota.resumo}</span>}
            {nota?.aviso && <span role="alert" className="text-[14px] text-alerta">{nota.aviso}</span>}
            {erroExcluir?.chave === k && <span role="alert" className="text-[14px] text-alerta">{erroExcluir.mensagem}</span>}
          </div>
        )
      })}
    </section>
  )
}
