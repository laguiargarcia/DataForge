import { useEffect, useState, type ComponentType } from 'react'
import { Financas } from './financas/Financas'
import { Lista } from './lista/Lista'

// A casca do ninho (df-8x0m.23.20.2): a barra embaixo troca entre os módulos, cada um com a sua tela
// em src/<modulo>/. Um módulo novo entra acrescentando um item em MODULOS; o primeiro é a tela inicial.
// Cada tela recebe `aberta`: montada o tempo todo, ela sabe quando aparece (a Lista relê ao abrir).

type Modulo = { id: string; rotulo: string; Tela: ComponentType<{ aberta: boolean }> }

const MODULOS: Modulo[] = [
  { id: 'financas', rotulo: 'Finanças', Tela: Financas },
  { id: 'lista', rotulo: 'Lista', Tela: Lista },
]
const INICIAL = MODULOS[0].id

/** O módulo do endereço (?tela=<modulo>), para recarregar na mesma tela; senão, o inicial. */
function telaInicial(): string {
  const pedido = new URLSearchParams(location.search).get('tela')
  return MODULOS.find(m => m.id === pedido)?.id ?? INICIAL
}

export function App() {
  const [tela, setTela] = useState(telaInicial)

  // Só o ?tela= é da casca: o resto do endereço (o ?mes= das finanças) fica, e a volta acha o mesmo mês.
  useEffect(() => {
    const endereco = new URLSearchParams(location.search)
    if (tela === INICIAL) endereco.delete('tela')
    else endereco.set('tela', tela)
    const busca = endereco.toString()
    history.replaceState(null, '', busca ? `?${busca}` : location.pathname)
  }, [tela])

  return (
    <>
      {/* As telas ficam montadas e só a aberta aparece: trocar de módulo não perde o que a outra tinha
          (o mês, uma linha aberta, um aplicar em curso). */}
      <div className="pb-[calc(56px+env(safe-area-inset-bottom))]">
        {MODULOS.map(({ id, Tela }) => (
          <div key={id} hidden={id !== tela}><Tela aberta={id === tela} /></div>
        ))}
      </div>
      <nav aria-label="Módulos"
        className="fixed bottom-0 inset-x-0 z-20 bg-cartao border-t border-linha pb-[env(safe-area-inset-bottom)]">
        <ul className="m-0 p-0 list-none max-w-[480px] mx-auto flex">
          {MODULOS.map(({ id, rotulo }) => (
            <li key={id} className="flex-1">
              <button type="button" aria-current={id === tela ? 'page' : undefined} onClick={() => setTela(id)}
                className={`w-full h-14 text-center font-medium ${id === tela ? 'text-acento' : 'text-secundario'}`}>
                {rotulo}
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </>
  )
}
