import { test, expect, type Page } from '@playwright/test'

// Guarda: requisição fora da origem, erro de console ou erro de página reprova o teste.
let problemas: string[] = []

test.beforeEach(async ({ page, baseURL }) => {
  problemas = []
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
  await expect(botao('Lista')).not.toHaveAttribute('aria-current')
  await expect(linhas(page).first()).toBeVisible()
  await page.getByRole('button', { name: 'Mês anterior' }).click()
  await expect(page.getByText('maio de 2026')).toBeVisible()

  await botao('Lista').click()
  await expect(page.getByRole('heading', { name: 'Lista', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Itens da lista' })).toContainText('A lista está vazia.')
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeHidden()
  await expect(botao('Lista')).toHaveAttribute('aria-current', 'page')
  await expect(page).toHaveURL(/\?mes=2026-05&tela=lista$/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  // Recarregar fica na mesma tela.
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Lista', exact: true })).toBeVisible()

  await botao('Finanças').click()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()
  await expect(page.getByText('maio de 2026')).toBeVisible()
  await expect(linhas(page).first()).toBeVisible()
  await expect(page).toHaveURL(/\?mes=2026-05$/)
})

test('na Lista, adiciona um item, recarrega, marca como comprado e limpa os comprados', async ({ page }) => {
  // A Lista fica montada e escondida atrás de Finanças: ela só pede /api/lista quando é aberta.
  const pedidosLista: string[] = []
  page.on('request', r => { if (new URL(r.url()).pathname.includes('/api/lista/')) pedidosLista.push(r.url()) })
  await page.goto(`?mes=${MES}`)
  await expect(linhas(page).first()).toBeVisible()
  expect(pedidosLista).toEqual([])
  await page.getByRole('navigation', { name: 'Módulos' }).getByRole('button', { name: 'Lista', exact: true }).click()

  const itens = page.getByRole('region', { name: 'Itens da lista' })
  await expect(itens).toContainText('A lista está vazia.')
  const campo = page.getByRole('textbox', { name: 'Novo item' })
  await campo.fill('biscoito de polvilho')
  await campo.press('Enter')   // Enter também adiciona; o campo limpa e fica com o foco
  await expect(campo).toHaveValue('')
  await expect(campo).toBeFocused()
  const aComprar = page.getByRole('list', { name: 'A comprar' })
  await expect(aComprar.getByRole('checkbox', { name: 'biscoito de polvilho' })).toBeVisible()
  await expect(aComprar).toContainText('lucas')   // quem adicionou: o nome digitado no login
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
  await expect(aComprar.getByRole('checkbox')).toHaveCount(2)
  await page.getByRole('button', { name: 'Apagar sabão em pó' }).click()
  await expect(aComprar.getByRole('checkbox')).toHaveCount(1)

  await aComprar.getByRole('checkbox', { name: 'biscoito de polvilho' }).click()
  const comprados = page.getByRole('list', { name: 'Comprados' })
  await expect(comprados.getByRole('checkbox', { name: 'biscoito de polvilho' })).toBeChecked()
  await expect(aComprar).toHaveCount(0)

  await page.getByRole('button', { name: 'Limpar comprados' }).click()
  await expect(itens).toContainText('A lista está vazia.')
  await expect(page.getByRole('button', { name: 'Limpar comprados' })).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
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
