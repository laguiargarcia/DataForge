// Mede o desenho de uma página: texto (por Range), botões, seções, cabeçalhos,
// barras (qualquer elemento com fundo ou borda superior) e ícones.
// Usado igualmente na captura da referência e nos testes, então os dois lados
// passam exatamente pelo mesmo código.
export async function medir(page) {
  return page.evaluate(() => {
    const normaliza = s => s.replace(/[  ]/g, ' ').replace(/\s+/g, ' ').trim()
    const estilo = el => {
      const c = getComputedStyle(el)
      return {
        color: c.color,
        backgroundColor: c.backgroundColor,
        fontFamily: c.fontFamily,
        fontSize: c.fontSize,
        fontWeight: c.fontWeight,
        letterSpacing: c.letterSpacing,
        lineHeight: c.lineHeight,
        borderRadius: c.borderRadius,
        padding: c.padding,
      }
    }
    const rect = r => ({ x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height })
    const visual = el => {
      const c = getComputedStyle(el)
      return (
        ['BUTTON', 'SECTION', 'HEADER', 'H2', 'SVG'].includes(el.tagName.toUpperCase()) ||
        (c.backgroundColor !== 'rgba(0, 0, 0, 0)' && el !== document.body && el !== document.documentElement) ||
        parseFloat(c.borderTopWidth) > 0
      )
    }
    const medidas = []
    const vistos = new Map()
    const chave = base => {
      const n = (vistos.get(base) ?? 0) + 1
      vistos.set(base, n)
      return `${base}#${n}`
    }
    const raiz = document.body
    const andador = document.createTreeWalker(raiz, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT)
    for (let no = andador.nextNode(); no; no = andador.nextNode()) {
      if (no.nodeType === Node.TEXT_NODE) {
        const pai = no.parentElement
        if (!pai || ['SCRIPT', 'STYLE'].includes(pai.tagName) || !no.textContent.trim()) continue
        const rg = document.createRange()
        rg.selectNodeContents(no)
        const r = rg.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) continue
        medidas.push({
          nome: chave(`texto:${normaliza(no.textContent)}`),
          rect: rect(r),
          estilo: estilo(pai),
        })
      } else {
        const el = no
        if (['SCRIPT', 'STYLE', 'PATH', 'X-DC', 'HELMET'].includes(el.tagName.toUpperCase())) continue
        if (!visual(el)) continue
        const r = el.getBoundingClientRect()
        if (r.width === 0 && r.height === 0) continue
        const rotulo = el.getAttribute('aria-label') || normaliza(el.innerText ?? '').slice(0, 40)
        medidas.push({
          nome: chave(`${el.tagName.toLowerCase()}:${rotulo}`),
          rect: rect(r),
          estilo: estilo(el),
        })
      }
    }
    return medidas
  })
}

// Compara duas listas de medidas. Devolve { ancoras, maiorDelta, diferencas }.
// Estilos precisam ser idênticos; retângulos, dentro de `tolerancia` px.
export function compararMedidas(atual, ref, tolerancia = 0.5) {
  const diferencas = []
  const mapa = l => new Map(l.map(m => [m.nome, m]))
  const a = mapa(atual)
  const r = mapa(ref)
  for (const nome of r.keys()) if (!a.has(nome)) diferencas.push(`falta no app: ${nome}`)
  for (const nome of a.keys()) if (!r.has(nome)) diferencas.push(`a mais no app: ${nome}`)
  let maiorDelta = 0
  let ancoras = 0
  for (const [nome, m] of a) {
    const o = r.get(nome)
    if (!o) continue
    ancoras++
    for (const k of ['x', 'y', 'width', 'height']) {
      const d = Math.abs(m.rect[k] - o.rect[k])
      if (d > maiorDelta) maiorDelta = d
      if (d > tolerancia) diferencas.push(`${nome} ${k}: app ${m.rect[k]} ref ${o.rect[k]}`)
    }
    for (const k of Object.keys(o.estilo)) {
      if (m.estilo[k] !== o.estilo[k]) diferencas.push(`${nome} ${k}: app "${m.estilo[k]}" ref "${o.estilo[k]}"`)
    }
  }
  return { ancoras, maiorDelta, diferencas }
}
