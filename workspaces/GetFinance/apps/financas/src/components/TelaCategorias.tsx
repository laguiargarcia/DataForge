import { excluirCategoria, excluirRegra, salvarCategoria, salvarRegra, textoEfeito, type Categoria, type Edicao, type Regra } from '../edicao'
import { TabelaEditavel } from './TabelaEditavel'

type Props = { edicao: Edicao; onMudou: () => void }

// Dicionário (subcategoria -> categoria -> Tipo) e regras (padrão -> subcategoria). Cada gravação
// vale na hora no banco; os números do Resumo mudam depois do Aplicar.
export function TelaCategorias({ edicao, onMudou }: Readonly<Props>) {
  const subcategorias = edicao.categorias.map(c => c.subcategoria)

  const gravarCategoria = async (antiga: string | null, c: Categoria) => {
    await salvarCategoria(antiga, c)
    onMudou()
    return null
  }
  const gravarRegra = async (antigo: string | null, r: Regra) => {
    const efeito = await salvarRegra(antigo, r)
    onMudou()
    return efeito ? textoEfeito(efeito, r.subcategoria) : null
  }
  const apagar = (excluir: (k: string) => Promise<void>) => async (k: string) => {
    await excluir(k)
    onMudou()
  }

  return (
    <div className="flex flex-col gap-5">
      <TabelaEditavel<Regra>
        titulo="Regras"
        colunas={[
          { campo: 'padrao', titulo: 'Padrão na descrição' },
          { campo: 'subcategoria', titulo: 'Subcategoria', opcoes: subcategorias },
        ]}
        linhas={edicao.regras}
        chave={r => r.padrao}
        nova={{ padrao: '', subcategoria: '' }}
        onSalvar={gravarRegra}
        onExcluir={apagar(excluirRegra)}
      />
      <TabelaEditavel<Categoria>
        titulo="Dicionário de categorias"
        colunas={[
          { campo: 'subcategoria', titulo: 'Subcategoria' },
          { campo: 'categoria', titulo: 'Categoria' },
          { campo: 'tipo', titulo: 'Tipo', opcoes: edicao.tipos },
        ]}
        linhas={edicao.categorias}
        chave={c => c.subcategoria}
        nova={{ subcategoria: '', categoria: '', tipo: '' }}
        onSalvar={gravarCategoria}
        onExcluir={apagar(excluirCategoria)}
      />
    </div>
  )
}
