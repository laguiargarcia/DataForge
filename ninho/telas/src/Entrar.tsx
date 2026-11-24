import { useRef, useState, type FormEvent } from 'react'
import { entrar } from './eu'
import { Logo } from './Logo'

// O formulário de login do ninho (df-8x0m.23.31.5), no lugar da caixa do navegador: no app da tela
// inicial do iPhone ela demorava e não guardava nada. É um <form> de verdade, com os nomes e os
// autocomplete que as Senhas do iCloud procuram, para o Safari preencher e oferecer guardar. A senha
// vive só no campo: vai ao servidor uma vez e o campo some quando a pessoa entra.

type Props = Readonly<{ aoEntrar: () => void }>

const classeCampo = 'w-full h-12 px-4 rounded-[12px] bg-cartao border border-linha text-texto text-[16px]'

export function Entrar({ aoEntrar }: Props) {
  const [nome, setNome] = useState('')
  const [senha, setSenha] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [falha, setFalha] = useState<string | null>(null)
  const campoSenha = useRef<HTMLInputElement>(null)

  function enviar(evento: FormEvent) {
    evento.preventDefault()
    if (enviando) return
    setEnviando(true)
    setFalha(null)
    entrar(nome, senha).then(
      certo => {
        if (certo) aoEntrar()
        else {
          setSenha('')
          setFalha('Nome ou senha errados.')
          campoSenha.current?.focus()   // o botão perdeu o foco ao ser desligado: o teclado fecharia
        }
      },
      () => setFalha('Não foi possível falar com o ninho. Confira a rede e tente de novo.'),
    ).finally(() => setEnviando(false))
  }

  return (
    <main className="min-h-dvh bg-fundo font-sans text-[16px] text-texto px-4 pt-[max(48px,env(safe-area-inset-top))] pb-8">
      <div className="max-w-[360px] mx-auto flex flex-col items-center gap-6">
        <Logo largura={132} />
        <form method="post" action="api/entrar" onSubmit={enviar} aria-label="Entrar no ninho" className="w-full flex flex-col gap-4">
          <h1 className="m-0 font-titulo text-[30px] leading-tight font-bold text-acento text-center">Entrar</h1>
          <label className="flex flex-col gap-1">
            <span className="text-[14px] text-secundario">Nome</span>
            <input type="text" name="username" autoComplete="username" autoCapitalize="none" autoCorrect="off"
              spellCheck={false} enterKeyHint="next" required value={nome} onChange={e => setNome(e.target.value)}
              className={classeCampo} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[14px] text-secundario">Senha</span>
            <input ref={campoSenha} type="password" name="password" autoComplete="current-password" enterKeyHint="go" required
              value={senha} onChange={e => setSenha(e.target.value)} className={classeCampo} />
          </label>
          {falha && <p role="alert" className="m-0 px-4 py-3 rounded-[12px] bg-alerta-fundo text-alerta">{falha}</p>}
          <button type="submit" disabled={enviando}
            className="h-12 rounded-[999px] bg-marca text-texto font-semibold text-center disabled:opacity-40">
            {enviando ? 'Entrando…' : 'Entrar'}
          </button>
        </form>
      </div>
    </main>
  )
}
