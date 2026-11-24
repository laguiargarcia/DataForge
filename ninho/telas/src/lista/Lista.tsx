// A tela do módulo lista (df-8x0m.23.20.2): por ora só o título e o aviso de vazia; adicionar, marcar,
// apagar e limpar, lendo /api/lista, vêm em df-8x0m.23.20.3.

export function Lista() {
  return (
    <div className="min-h-dvh bg-fundo font-sans text-[16px] text-texto">
      <header className="sticky top-0 z-10 bg-fundo pt-[max(12px,env(safe-area-inset-top))] pb-2 px-4">
        <div className="max-w-[480px] mx-auto">
          <h1 className="m-0 text-[22px] font-semibold">Lista</h1>
        </div>
      </header>
      <main className="max-w-[480px] mx-auto px-4 pb-[max(24px,env(safe-area-inset-bottom))]">
        <section className="rounded-[14px] bg-cartao overflow-hidden" aria-label="Itens da lista">
          <p className="m-0 px-4 py-6 text-secundario">A lista está vazia.</p>
        </section>
      </main>
    </div>
  )
}
