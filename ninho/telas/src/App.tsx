import { useCallback, useEffect, useState, type ComponentType } from 'react'
import { lerEu, permitidos, telaAberta, type Eu } from './eu'
import { Desejos } from './desejos/Desejos'
import { Financas } from './financas/Financas'
import { Lista } from './lista/Lista'

// A casca do ninho (df-8x0m.23.20.2): a barra embaixo troca entre os módulos, cada um com a sua tela
// em src/<modulo>/. Um módulo novo entra acrescentando um item em MODULOS; o primeiro é a tela inicial.
// Cada tela recebe `aberta`: montada o tempo todo, ela sabe quando aparece (a Lista relê ao abrir);
// e `eu`, quem entrou (os Desejos abrem na parte dessa pessoa).
// A casca pergunta quem entrou antes de montar qualquer tela: só os módulos da pessoa aparecem na
// barra e só as telas deles são montadas (df-8x0m.23.6.3).

type Modulo = { id: string; rotulo: string; Tela: ComponentType<{ aberta: boolean; eu: Eu }> }
type Carga = { fase: 'carregando' } | { fase: 'erro'; mensagem: string } | { fase: 'ok'; eu: Eu }

const MODULOS: Modulo[] = [
  { id: 'financas', rotulo: 'Finanças', Tela: Financas },
  { id: 'lista', rotulo: 'Compras', Tela: Lista },
  { id: 'desejos', rotulo: 'Desejos', Tela: Desejos },
]

const telaPedida = () => new URLSearchParams(location.search).get('tela')

function Aviso({ texto, tentar }: Readonly<{ texto: string; tentar?: () => void }>) {
  return (
    <main className="min-h-dvh bg-fundo font-sans text-[16px] text-texto px-4 pt-[max(24px,env(safe-area-inset-top))]">
      <div className="max-w-[480px] mx-auto rounded-[14px] bg-cartao px-4 py-6 flex flex-col gap-3 items-start">
        <p className="m-0 break-words">{texto}</p>
        {tentar && (
          <button type="button" onClick={tentar} className="h-11 px-5 rounded-[999px] bg-marca text-texto font-semibold">
            Tentar de novo
          </button>
        )}
      </div>
    </main>
  )
}

export function App() {
  const [carga, setCarga] = useState<Carga>({ fase: 'carregando' })
  const [escolhida, setEscolhida] = useState(telaPedida)

  const entrar = useCallback(() => {
    setCarga({ fase: 'carregando' })
    lerEu().then(eu => setCarga({ fase: 'ok', eu }),
      (e: unknown) => setCarga({ fase: 'erro', mensagem: e instanceof Error ? e.message : String(e) }))
  }, [])
  useEffect(entrar, [entrar])

  const modulos = carga.fase === 'ok' ? permitidos(MODULOS, carga.eu) : []
  // Um ?tela= de um módulo que a pessoa não tem cai no primeiro que ela tem.
  const tela = telaAberta(modulos, escolhida)
  const inicial = modulos[0]?.id

  // Só o ?tela= é da casca: o resto do endereço (o ?mes= das finanças) fica, e a volta acha o mesmo mês.
  useEffect(() => {
    if (tela === null) return
    const endereco = new URLSearchParams(location.search)
    if (tela === inicial) endereco.delete('tela')
    else endereco.set('tela', tela)
    const busca = endereco.toString()
    history.replaceState(null, '', busca ? `?${busca}` : location.pathname)
  }, [tela, inicial])

  if (carga.fase === 'carregando') return <Aviso texto="Carregando…" />
  if (carga.fase === 'erro') return <Aviso texto={`Não foi possível saber quem entrou: ${carga.mensagem}`} tentar={entrar} />
  if (tela === null) return <Aviso texto={`${carga.eu.nome}, você ainda não tem acesso a nenhuma parte do ninho.`} />

  return (
    <>
      {/* As telas ficam montadas e só a aberta aparece: trocar de módulo não perde o que a outra tinha
          (o mês, uma linha aberta, um aplicar em curso). */}
      <div className="pb-[calc(56px+env(safe-area-inset-bottom))]">
        {modulos.map(({ id, Tela }) => (
          <div key={id} hidden={id !== tela}><Tela aberta={id === tela} eu={carga.eu} /></div>
        ))}
      </div>
      <nav aria-label="Módulos"
        className="fixed bottom-0 inset-x-0 z-20 bg-cartao border-t border-linha pb-[env(safe-area-inset-bottom)]">
        <ul className="m-0 p-0 list-none max-w-[480px] mx-auto flex">
          {modulos.map(({ id, rotulo }) => (
            <li key={id} className="flex-1">
              <button type="button" aria-current={id === tela ? 'page' : undefined} onClick={() => setEscolhida(id)}
                className={`w-full h-14 flex items-center justify-center font-semibold ${id === tela ? 'text-texto' : 'text-secundario'}`}>
                {/* O item ativo ganha o azul da marca de fundo, numa pílula: o texto fica no tom escuro. */}
                <span className={`px-4 py-1.5 rounded-[999px] ${id === tela ? 'bg-marca' : ''}`}>{rotulo}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </>
  )
}
