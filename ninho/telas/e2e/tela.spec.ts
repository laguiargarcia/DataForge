import { test, expect, type BrowserContext, type Page } from '@playwright/test'
import { login, soLista } from '../playwright.config'

// Abre uma sessão da pessoa no servidor: o cookie fica no contexto, e a página já abre com ele.
// Estes testes são das telas, então entram direto pela rota; o formulário de login tem os seus em
// e2e/entrar.spec.ts.
async function entrar(contexto: BrowserContext, quem: { nome: string; senha: string }) {
  const r = await contexto.request.post('api/entrar', { data: quem })
  expect(r.status()).toBe(204)
}

// Guarda: requisição fora da origem, erro de console ou erro de página reprova o teste.
let problemas: string[] = []

test.beforeEach(async ({ page, baseURL }) => {
  problemas = []
  await entrar(page.context(), login)
  const origem = new URL(baseURL!).origin
  page.on('request', r => {
    const u = r.url()
    if (!u.startsWith(origem) && !u.startsWith('data:')) problemas.push(`requisição externa: ${u}`)
  })
  page.on('console', m => { if (m.type() === 'error') problemas.push(`console: ${m.text()}`) })
  page.on('pageerror', e => problemas.push(`pageerror: ${e.message}`))
})

test.afterEach(() => {
  expect(problemas).toEqual([])
})

// Junho de 2026 tem transações no finance_demo (gerador sintético determinístico).
const MES = '2026-06'
const linhas = (page: Page) => page.getByRole('region', { name: 'Transações do mês' }).getByRole('listitem')
const pendencia = (page: Page) => page.getByText('Há edições não aplicadas')

test('abre no mês do endereço, cabe na largura do iPhone e troca de mês', async ({ page }) => {
  await page.goto(`?mes=${MES}`)
  await expect(page.getByText('junho de 2026')).toBeVisible()
  await expect(linhas(page).first()).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Mês anterior' }).click()
  await expect(page.getByText('maio de 2026')).toBeVisible()
  await expect(page).toHaveURL(/\?mes=2026-05$/)
  await expect(linhas(page).first()).toBeVisible()
})

test('a barra embaixo abre a Lista e volta para Finanças no mesmo mês', async ({ page }) => {
  const barra = page.getByRole('navigation', { name: 'Módulos' })
  const botao = (rotulo: string) => barra.getByRole('button', { name: rotulo, exact: true })
  await page.goto(`?mes=${MES}`)
  await expect(botao('Finanças')).toHaveAttribute('aria-current', 'page')
  await expect(botao('Compras')).not.toHaveAttribute('aria-current')
  await expect(linhas(page).first()).toBeVisible()
  await page.getByRole('button', { name: 'Mês anterior' }).click()
  await expect(page.getByText('maio de 2026')).toBeVisible()

  await botao('Compras').click()
  await expect(page.getByRole('heading', { name: 'Compras', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Itens da lista' })).toContainText('A lista está vazia.')
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeHidden()
  await expect(botao('Compras')).toHaveAttribute('aria-current', 'page')
  await expect(page).toHaveURL(/\?mes=2026-05&tela=lista$/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // Recarregar fica na mesma tela.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Compras', exact: true })).toBeVisible()

  await botao('Finanças').click()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()
  await expect(page.getByText('maio de 2026')).toBeVisible()
  await expect(linhas(page).first()).toBeVisible()
  await expect(page).toHaveURL(/\?mes=2026-05$/)
})

test('quem só tem a lista vê a barra sem Finanças nem Desejos, abre direto na Lista e não pede nada às finanças', async ({ browser, baseURL }) => {
  // Outra pessoa, outro login: um contexto próprio com a sessão dela (df-8x0m.23.6.3).
  const contexto = await browser.newContext({ baseURL })
  await entrar(contexto, soLista)
  const page = await contexto.newPage()
  const pedidos: string[] = []
  const erros: string[] = []
  page.on('request', r => pedidos.push(new URL(r.url()).pathname))
  page.on('console', m => { if (m.type() === 'error') erros.push(m.text()) })
  page.on('pageerror', e => erros.push(e.message))

  // O endereço pede as finanças, que ela não tem: cai na primeira tela dela.
  await page.goto(`?tela=financas&mes=${MES}`)
  await expect(page.getByRole('heading', { name: 'Compras', exact: true })).toBeVisible()
  const barra = page.getByRole('navigation', { name: 'Módulos' })
  // Só o módulo dela e o Sair, que é da casca.
  await expect(barra.getByRole('button')).toHaveText(['Compras', 'Sair'])
  await expect(barra.getByRole('button', { name: 'Compras', exact: true })).toHaveAttribute('aria-current', 'page')
  await expect(barra.getByRole('button', { name: 'Desejos', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Desejos', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Transações' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Itens da lista' })).toContainText('A lista está vazia.')
  expect(pedidos).toContain('/api/eu')
  expect(pedidos.filter(p => p.startsWith('/api/financas') || p.startsWith('/api/desejos'))).toEqual([])
  expect(erros).toEqual([])
  await contexto.close()
})

test('na Lista, adiciona um item, recarrega, marca como comprado e limpa os comprados', async ({ page }) => {
  // A Lista fica montada e escondida atrás de Finanças: ela só pede /api/lista quando é aberta.
  const pedidosLista: string[] = []
  page.on('request', r => { if (new URL(r.url()).pathname.includes('/api/lista/')) pedidosLista.push(r.url()) })
  await page.goto(`?mes=${MES}`)
  await expect(linhas(page).first()).toBeVisible()
  expect(pedidosLista).toEqual([])
  await page.getByRole('navigation', { name: 'Módulos' }).getByRole('button', { name: 'Compras', exact: true }).click()

  const itens = page.getByRole('region', { name: 'Itens da lista' })
  await expect(itens).toContainText('A lista está vazia.')
  const campo = page.getByRole('textbox', { name: 'Novo item' })
  await campo.fill('biscoito de polvilho')
  await campo.press('Enter')   // Enter também adiciona; o campo limpa e fica com o foco
  await expect(campo).toHaveValue('')
  await expect(campo).toBeFocused()
  const aComprar = page.getByRole('list', { name: 'A comprar' })
  await expect(aComprar.getByRole('checkbox', { name: 'biscoito de polvilho' })).toBeVisible()
  await expect(aComprar).toContainText('ana')   // quem adicionou: a pessoa que entrou
  await expect(page.getByRole('button', { name: 'Limpar comprados' })).toHaveCount(0)

  // Os alvos de toque têm pelo menos 44 px e a tela cabe na largura do iPhone.
  for (const alvo of [aComprar.locator('label').first(), page.getByRole('button', { name: 'Apagar biscoito de polvilho' }),
    page.getByRole('button', { name: 'Adicionar' })]) {
    const caixa = (await alvo.boundingBox())!
    expect(Math.min(caixa.width, caixa.height)).toBeGreaterThanOrEqual(44)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.reload()
  await expect(aComprar.getByRole('checkbox', { name: 'biscoito de polvilho' })).toBeVisible()

  // Um segundo item, pelo botão, e apagado.
  await campo.fill('sabão em pó')
  await page.getByRole('button', { name: 'Adicionar' }).click()
  await expect(campo).toBeFocused()   // pelo botão o foco também fica no campo (df-8x0m.23.20.7)
  await expect(aComprar.getByRole('checkbox')).toHaveCount(2)
  await page.getByRole('button', { name: 'Apagar sabão em pó' }).click()
  await expect(aComprar.getByRole('checkbox')).toHaveCount(1)

  await aComprar.getByRole('checkbox', { name: 'biscoito de polvilho' }).click()
  const comprados = page.getByRole('list', { name: 'Comprados' })
  await expect(comprados.getByRole('checkbox', { name: 'biscoito de polvilho' })).toBeChecked()
  await expect(aComprar).toHaveCount(0)

  await page.getByRole('button', { name: 'Limpar comprados' }).click()
  // Limpar pede confirmação (df-8x0m.23.20.12): o toque não apaga, Cancelar mantém, Apagar apaga.
  const confirmar = page.getByRole('group', { name: 'Confirmar limpeza' })
  await expect(confirmar).toContainText('Apagar 1 item comprado?')
  await expect(comprados.getByRole('checkbox')).toHaveCount(1)
  await confirmar.getByRole('button', { name: 'Cancelar' }).click()
  await expect(confirmar).toHaveCount(0)
  await expect(comprados.getByRole('checkbox')).toHaveCount(1)
  await page.getByRole('button', { name: 'Limpar comprados' }).click()
  await confirmar.getByRole('button', { name: 'Apagar', exact: true }).click()
  await expect(itens).toContainText('A lista está vazia.')
  await expect(page.getByRole('button', { name: 'Limpar comprados' })).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('nos Desejos, cria um desejo dos dois com um texto e um link, move, marca realizado e apaga', async ({ page }) => {
  const LINK = 'https://exemplo.invalid/bicicleta'
  // Montada e escondida, a tela dos desejos não pede nada antes de ser aberta.
  const pedidosDesejos: string[] = []
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/desejos')) pedidosDesejos.push(r.url()) })
  await page.goto(`?mes=${MES}`)
  await expect(linhas(page).first()).toBeVisible()
  expect(pedidosDesejos).toEqual([])
  await page.getByRole('navigation', { name: 'Módulos' }).getByRole('button', { name: 'Desejos', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Desejos', exact: true })).toBeVisible()
  await expect(page).toHaveURL(/tela=desejos/)

  // A primeira vez abre na parte de quem entrou.
  const partes = page.getByRole('group', { name: 'De quem' })
  const parte = (rotulo: string) => partes.getByRole('button', { name: rotulo, exact: true })
  await expect(parte('ana')).toHaveAttribute('aria-pressed', 'true')
  await expect(parte('beto')).toHaveAttribute('aria-pressed', 'false')
  const regiao = page.getByRole('region', { name: 'Desejos da parte' })
  await expect(regiao).toContainText('Nenhum desejo aqui ainda.')

  await parte('Nós dois').click()
  await expect(parte('Nós dois')).toHaveAttribute('aria-pressed', 'true')
  const campo = page.getByRole('textbox', { name: 'Novo desejo' })
  await campo.fill('bicicleta')
  await campo.press('Enter')
  await expect(campo).toHaveValue('')
  const abertos = page.getByRole('list', { name: 'Desejos', exact: true })
  const titulo = abertos.getByRole('button', { name: 'bicicleta', exact: true })
  await expect(titulo).toBeVisible()

  // Tocar abre o desejo: duas anotações, um texto e um endereço.
  await titulo.click()
  await expect(titulo).toHaveAttribute('aria-expanded', 'true')
  const nota = page.getByRole('textbox', { name: 'Nova anotação' })
  await nota.fill('a azul, aro 29')
  await nota.press('Enter')
  const anotacoes = page.getByRole('list', { name: 'Anotações de bicicleta' })
  await expect(anotacoes.getByRole('listitem')).toHaveCount(1)
  await nota.fill(LINK)
  await page.getByRole('button', { name: 'Anotar' }).click()
  await expect(anotacoes.getByRole('listitem')).toHaveCount(2)
  await expect(anotacoes.getByRole('listitem').first()).toHaveText('a azul, aro 29')
  await expect(anotacoes.getByRole('link')).toHaveCount(1)

  // O endereço é um link que abre em outra aba; não é seguido (o domínio .invalid não existe).
  const link = anotacoes.getByRole('link', { name: LINK })
  await expect(link).toHaveAttribute('href', LINK)
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')

  // Alvos de toque de pelo menos 44 px, e a tela cabe na largura do iPhone.
  for (const alvo of [parte('Nós dois'), titulo, link, page.getByRole('button', { name: `Apagar a anotação ${LINK}` }),
    page.getByRole('button', { name: 'Anotar' }), page.getByRole('combobox'), page.getByRole('button', { name: 'Realizado' }),
    page.getByRole('button', { name: 'Apagar desejo' })]) {
    const caixa = (await alvo.boundingBox())!
    expect(Math.min(caixa.width, caixa.height)).toBeGreaterThanOrEqual(44)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // Recarregar volta na mesma parte (guardada no aparelho), com o link.
  await page.reload()
  await expect(parte('Nós dois')).toHaveAttribute('aria-pressed', 'true')
  await expect(anotacoes.getByRole('link', { name: LINK })).toHaveAttribute('target', '_blank')

  // Mover para a parte da ana: some daqui e aparece lá.
  await titulo.click()
  await page.getByRole('combobox').selectOption({ label: 'ana' })
  await expect(regiao).toContainText('Nenhum desejo aqui ainda.')
  await parte('ana').click()
  await expect(titulo).toBeVisible()
  await expect(anotacoes.getByRole('listitem')).toHaveCount(2)

  // Realizado: sai dos abertos e fica no arquivo recolhido, de onde é apagado.
  await titulo.click()
  await page.getByRole('button', { name: 'Realizado' }).click()
  await expect(regiao).toContainText('Nenhum desejo em aberto.')
  const realizados = page.getByRole('list', { name: 'Realizados' })
  await expect(realizados).toBeHidden()
  await page.getByText('Realizados (1)').click()
  const realizado = realizados.getByRole('button', { name: 'bicicleta', exact: true })
  await expect(realizado).toBeVisible()
  await realizado.click()
  await expect(page.getByRole('button', { name: 'Desmarcar realizado' })).toBeVisible()
  await page.getByRole('button', { name: 'Apagar desejo' }).click()
  const confirmar = page.getByRole('group', { name: 'Confirmar apagar' })
  await expect(confirmar).toContainText('Apagar “bicicleta”?')
  await confirmar.getByRole('button', { name: 'Cancelar' }).click()
  await expect(confirmar).toHaveCount(0)
  await expect(realizado).toBeVisible()
  await page.getByRole('button', { name: 'Apagar desejo' }).click()
  await confirmar.getByRole('button', { name: 'Apagar', exact: true }).click()
  await expect(regiao).toContainText('Nenhum desejo aqui ainda.')
  await expect(page.getByText('Realizados (1)')).toHaveCount(0)
  await parte('Nós dois').click()
  await expect(regiao).toContainText('Nenhum desejo aqui ainda.')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

// Um "a página voltou a ficar visível" (o iPhone desbloqueado): a Lista relê.
const voltarAVer = (page: Page) => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))

test('na Lista, uma releitura que falha mantém os itens e um toque não volta atrás com uma leitura velha', async ({ page }) => {
  await page.goto('?tela=lista')
  const itens = page.getByRole('region', { name: 'Itens da lista' })
  await expect(itens).toContainText('A lista está vazia.')
  const campo = page.getByRole('textbox', { name: 'Novo item' })
  await campo.fill('fermento')
  await campo.press('Enter')
  const aComprar = page.getByRole('list', { name: 'A comprar' })
  const fermento = page.getByRole('checkbox', { name: 'fermento' })
  await expect(aComprar.getByRole('checkbox', { name: 'fermento' })).toBeVisible()

  // A rede ainda acordando: a releitura falha, a lista fica e um aviso aparece.
  const leitura = '**/api/lista/itens'
  await page.route(leitura, r => (r.request().method() === 'GET'
    ? r.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"sem rede"}' })
    : r.fallback()))
  const falhou = page.waitForResponse(r => r.url().endsWith('/api/lista/itens') && r.status() === 503)
  await voltarAVer(page)
  await falhou
  await expect(page.getByRole('alert')).toContainText('sem rede')
  await expect(aComprar.getByRole('checkbox', { name: 'fermento' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Tentar de novo' })).toHaveCount(0)

  // A rede voltou: uma releitura boa tira o aviso.
  await page.unroute(leitura)
  await voltarAVer(page)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(fermento).not.toBeChecked()

  // Uma leitura pedida antes do toque e respondida depois dele (com a lista de antes) não desfaz a
  // marca. A marca fica presa no caminho até a conferência, para a leitura velha chegar antes dela.
  let soltarVelha!: () => void
  let soltarMarca!: () => void
  const velhaSolta = new Promise<void>(ok => { soltarVelha = ok })
  const marcaSolta = new Promise<void>(ok => { soltarMarca = ok })
  let leituras = 0
  await page.route(leitura, async r => {
    if (r.request().method() !== 'GET' || ++leituras > 1) return r.fallback()
    const resposta = await r.fetch()
    await velhaSolta
    await r.fulfill({ response: resposta })
  })
  await page.route('**/api/lista/itens/*', async r => {
    if (r.request().method() === 'PATCH') await marcaSolta
    await r.fallback()
  })
  await voltarAVer(page)
  await expect.poll(() => leituras).toBe(1)
  await fermento.click()
  await expect(fermento).toBeChecked()
  const velha = page.waitForResponse(r => r.url().endsWith('/api/lista/itens') && r.request().method() === 'GET')
  soltarVelha()
  await velha
  await page.waitForTimeout(300)   // a resposta velha já foi lida e descartada
  await expect(fermento).toBeChecked({ timeout: 0 })
  soltarMarca()
  await expect(page.getByRole('list', { name: 'Comprados' }).getByRole('checkbox', { name: 'fermento' })).toBeChecked()
  await page.unrouteAll()

  await page.getByRole('button', { name: 'Limpar comprados' }).click()
  await page.getByRole('group', { name: 'Confirmar limpeza' }).getByRole('button', { name: 'Apagar', exact: true }).click()
  await expect(itens).toContainText('A lista está vazia.')
  await expect(page.getByRole('alert')).toHaveCount(0)
  // O 503 de propósito aparece no console do navegador; só ele é esperado.
  problemas = problemas.filter(p => !p.startsWith('console: Failed to load resource: the server responded with a status of 503'))
})

test('corrige a categoria de uma transação, aplica e vê a categoria nova', async ({ page }) => {
  // Um aplicar em curso nunca vira dois (df-8x0m.23.13.10): toque duplo e recarregar não pedem outro.
  const pedidosAplicar: string[] = []
  const acompanhados: string[] = []
  page.on('request', r => {
    const caminho = new URL(r.url()).pathname
    if (r.method() === 'POST' && caminho.endsWith('/api/financas/aplicar')) pedidosAplicar.push(caminho)
    if (r.method() === 'GET' && caminho.includes('/api/financas/aplicar/')) acompanhados.push(caminho)
  })
  await page.goto(`?mes=${MES}`)
  const linha = linhas(page).first()
  const categoria = linha.locator('[data-campo="categoria"]')
  const antes = await categoria.innerText()
  await expect(pendencia(page)).toHaveCount(0)

  await linha.getByRole('button').first().click()
  const escolha = linha.getByLabel('Categoria')
  const opcoes = await escolha.locator('option:not([disabled])').allInnerTexts()
  const nova = opcoes.find(o => o !== antes)!
  await escolha.selectOption(nova)
  await linha.getByRole('button', { name: 'Salvar' }).click()

  await expect(pendencia(page)).toBeVisible()
  await expect(linha).toContainText(`→ ${nova} (falta aplicar)`)
  await expect(categoria).toHaveText(antes)   // a tabela publicada só muda no aplicar

  await page.getByRole('button', { name: 'Aplicar', exact: true }).dblclick()
  await expect(page.getByRole('status')).toContainText('Aplicando')
  await expect.poll(() => acompanhados.length).toBeGreaterThan(0)
  const antesDeRecarregar = acompanhados.length
  await page.reload()
  // A página recarregada volta a seguir a execução guardada, sem oferecer "Aplicar".
  await expect.poll(() => acompanhados.length).toBeGreaterThan(antesDeRecarregar)
  await expect(page.getByRole('button', { name: 'Aplicar', exact: true })).toHaveCount(0)
  await expect(page.getByRole('status')).toHaveCount(0, { timeout: 150_000 })
  expect(pedidosAplicar).toHaveLength(1)
  expect(new Set(acompanhados).size).toBe(1)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(pendencia(page)).toHaveCount(0)
  await expect(categoria).toHaveText(nova)
  await expect(linha).toContainText('corrigida')
  await expect(linha).not.toContainText('falta aplicar')

  await page.reload()
  await expect(linhas(page).first().locator('[data-campo="categoria"]')).toHaveText(nova)
  await expect(pendencia(page)).toHaveCount(0)
})

test('a página servida mostra o logo do ninho e tem os ícones da identidade', async ({ page, request }) => {
  await page.goto(`?mes=${MES}`)
  const logo = page.getByRole('img', { name: 'ninho' }).first()
  await expect(logo).toBeVisible()
  expect(await logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  for (const rel of ['icon', 'apple-touch-icon']) {
    const href = await page.locator(`link[rel="${rel}"]`).getAttribute('href')
    const resposta = await request.get(new URL(href!, page.url()).href)
    expect(resposta.status()).toBe(200)
    expect(resposta.headers()['content-type']).toBe('image/png')
  }
})
