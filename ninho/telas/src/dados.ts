import { useCallback, useEffect, useRef, useState } from 'react'

// O que uma tela de módulo lê do servidor e como ela muda (df-8x0m.23.22.3): comum à Lista e aos
// Desejos. Os dados são dos dois: lidos ao abrir a tela e de novo cada vez que ela ou a página volta
// a aparecer, porque a outra pessoa pode ter mexido. Precisa de rede: sem guarda local nem fila
// (df-8x0m.23.8).

export type Carga<T> = { fase: 'carregando' } | { fase: 'erro'; mensagem: string } | { fase: 'ok'; valor: T }

export const mensagem = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** Lê com `ler` (uma função estável, do módulo) enquanto a tela está `aberta`; `oQue` entra no aviso
 * de uma releitura que falha ("Não foi possível reler <oQue>: ..."). */
export function useDados<T>(aberta: boolean, ler: () => Promise<T>, oQue: string) {
  const [carga, setCarga] = useState<Carga<T>>({ fase: 'carregando' })
  const [ocupado, setOcupado] = useState(false)
  const [falha, setFalha] = useState<string | null>(null)
  // Só a leitura mais recente vale: uma resposta atrasada não apaga outra mais nova nem uma mudança
  // feita depois que ela foi pedida (alterar também conta).
  const ultima = useRef(0)
  const carregou = useRef(false)

  // Com os dados já na tela, uma releitura que falha (o iPhone desbloqueado com a rede ainda voltando)
  // mantém o que está lá e mostra o aviso; o estado de erro com "Tentar de novo" é só para a primeira
  // carga. Uma releitura boa tira o aviso, salvo quando ela fecha uma mudança que falhou (`manterFalha`).
  // Responde se a leitura falhou sendo ainda a mais recente (quem pediu pode ter o que desfazer).
  const reler = useCallback((manterFalha = false): Promise<boolean> => {
    const esta = ++ultima.current
    return ler().then(
      valor => {
        if (ultima.current !== esta) return false
        carregou.current = true
        setCarga({ fase: 'ok', valor })
        if (!manterFalha) setFalha(null)
        return false
      },
      e => {
        if (ultima.current !== esta) return false
        if (!carregou.current) setCarga({ fase: 'erro', mensagem: mensagem(e) })
        else if (!manterFalha) setFalha(`Não foi possível reler ${oQue}: ${mensagem(e)}`)
        return true
      },
    )
  }, [ler, oQue])

  // A casca mantém a tela montada e escondida: ler só quando ela está aberta, e reler quando a
  // página volta a ficar visível (outra aba, o iPhone desbloqueado).
  useEffect(() => {
    if (!aberta) return
    reler()
    const aoVoltar = () => { if (document.visibilityState === 'visible') reler() }
    document.addEventListener('visibilitychange', aoVoltar)
    return () => document.removeEventListener('visibilitychange', aoVoltar)
  }, [aberta, reler])

  // Cada mudança vai ao servidor e depois relê tudo, com a ordem do servidor e o que a outra pessoa
  // tenha mudado; se falhar, relê também (o que se mudou pode ter sido apagado do outro lado).
  // Uma mudança com `antes` já aparece no toque; a releitura confirma ou desfaz. Sem rede, a mudança
  // falha e a releitura também: aí a tela volta ao que mostrava antes do toque, em vez de ficar com
  // uma mudança que não aconteceu (df-8x0m.23.22.7).
  const alterar = useCallback((acao: () => Promise<unknown>, antes?: (valor: T) => T) => {
    setOcupado(true)
    setFalha(null)
    ++ultima.current   // uma leitura pedida antes do toque e respondida depois não desfaz a mudança
    let anterior: Carga<T> | null = null
    if (antes) {
      setCarga(c => {
        if (c.fase !== 'ok') return c
        anterior = c
        return { fase: 'ok', valor: antes(c.valor) }
      })
    }
    acao()
      .then(() => reler(), async (e: unknown) => {
        setFalha(mensagem(e))
        if (await reler(true) && anterior) setCarga(anterior)
      })
      .finally(() => setOcupado(false))
  }, [reler])

  /** A primeira carga falhou: volta a "Carregando…" e lê de novo. */
  const tentar = useCallback(() => { setCarga({ fase: 'carregando' }); reler() }, [reler])

  return { carga, ocupado, falha, reler, alterar, tentar }
}
