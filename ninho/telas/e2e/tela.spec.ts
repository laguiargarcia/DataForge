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

test('corrige a categoria de uma transação, aplica e vê a categoria nova', async ({ page }) => {
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

  await page.getByRole('button', { name: 'Aplicar', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Aplicando')
  await expect(page.getByRole('status')).toHaveCount(0, { timeout: 150_000 })
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(pendencia(page)).toHaveCount(0)
  await expect(categoria).toHaveText(nova)
  await expect(linha).toContainText('corrigida')
  await expect(linha).not.toContainText('falta aplicar')

  await page.reload()
  await expect(linhas(page).first().locator('[data-campo="categoria"]')).toHaveText(nova)
  await expect(pendencia(page)).toHaveCount(0)
})
