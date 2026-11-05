import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { compararMedidas, medir } from './medir.mjs'

const referencia = JSON.parse(readFileSync(join(import.meta.dirname, 'referencia', 'medidas.json'), 'utf8'))

// Guarda: qualquer requisição fora da origem, erro de console ou erro de página reprova o teste.
let problemas: string[] = []
let permitidos: RegExp[] = []

test.beforeEach(async ({ page, baseURL }) => {
  problemas = []
  permitidos = []
  const origem = new URL(baseURL!).origin
  page.on('request', r => {
    const u = r.url()
    if (!u.startsWith(origem) && !u.startsWith('data:')) problemas.push(`requisição externa: ${u}`)
  })
  page.on('console', m => {
    if (m.type() === 'error' && !permitidos.some(p => p.test(m.text()))) problemas.push(`console: ${m.text()}`)
  })
  page.on('pageerror', e => problemas.push(`pageerror: ${e.message}`))
})

test.afterEach(() => {
  expect(problemas).toEqual([])
})

const abrir = async (page: Page) => {
  await page.goto('')
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await expect(page.getByRole('button', { name: /^Mercado/ })).toBeVisible()
}
// toHaveText normaliza espaços, inclusive NBSP (\s casa U+00A0), então R$ 1,00 com NBSP ou espaço comum é igual.
// Valor da faixa: o span irmão do rótulo.
const faixa = (page: Page, rotulo: string) => page.getByText(rotulo, { exact: true }).locator('xpath=following-sibling::span')
const mes = (page: Page, texto: string) => page.getByText(texto, { exact: true })
const anterior = (page: Page) => page.getByRole('button', { name: 'Mês anterior' })
const proximo = (page: Page) => page.getByRole('button', { name: 'Próximo mês' })

async function voltarAte(page: Page, rotulo: string) {
  for (let i = 0; i < 12 && !(await mes(page, rotulo).isVisible()); i++) await anterior(page).click()
  await expect(mes(page, rotulo)).toBeVisible()
}

test('junho de 2026 mostra os valores de referência', async ({ page }) => {
  await abrir(page)
  await expect(mes(page, 'junho de 2026')).toBeVisible()
  await expect(page.getByText('Sobrou este mês').locator('..')).toContainText('+R$ 5.705,02')
  await expect(page.getByText('Entrou').first()).toHaveText('Entrou R$ 10.555,36')
  await expect(page.getByText('Saiu', { exact: false }).first()).toHaveText('Saiu R$ 4.850,34')
  await expect(faixa(page, 'Fatura pendente')).toHaveText('R$ 1.279,19')
  await expect(faixa(page, 'Na conta')).toHaveText('R$ 5.230,55')
  await expect(faixa(page, 'Investido')).toHaveText('R$ 53.212,32')
})

test('trocar de mês atualiza tudo e dezembro de 2025 mostra — na fatura', async ({ page }) => {
  await abrir(page)
  await voltarAte(page, 'maio de 2026')
  await expect(faixa(page, 'Fatura pendente')).toHaveText('R$ 0,00')
  await voltarAte(page, 'dezembro de 2025')
  await expect(page.getByText('Entrou').first()).toHaveText('Entrou R$ 2.307,20')
  await expect(page.getByText('Saiu', { exact: false }).first()).toHaveText('Saiu R$ 2.617,17')
  await expect(page.getByText('Sobrou este mês').locator('..')).toContainText('-R$ 309,97')
  await expect(faixa(page, 'Fatura pendente')).toHaveText('—')
  await expect(anterior(page)).toBeDisabled()
})

test('não existe mês depois de junho', async ({ page }) => {
  await abrir(page)
  await expect(proximo(page)).toBeDisabled()
  await expect(page.getByText('julho de 2026')).toHaveCount(0)
  await expect(page.getByText('2026-07')).toHaveCount(0)
})

test('drill-down abre, troca e volta', async ({ page }) => {
  await abrir(page)
  const lista = page.getByRole('heading', { name: 'Para onde foi' })
  await page.getByRole('button', { name: /^Mercado/ }).click()
  await expect(page.getByText('Mercado · 7 transações · 32% do que saiu')).toBeVisible()
  await expect(page.getByText('Mercado Central')).toHaveCount(7)
  await expect(page.getByText('Mercado Central').first().locator('..')).toContainText('17/06 · Cartão')
  await expect(lista).toBeVisible()

  await page.getByRole('button', { name: /^Saúde/ }).click()
  await expect(page.getByText('Farmacia Sao Joao').first()).toBeVisible()
  await expect(page.getByText('Mercado Central')).toHaveCount(0)
  await expect(lista).toBeVisible()

  await page.getByRole('button', { name: /^Saúde/ }).click()
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await expect(lista).toBeVisible()

  await page.getByRole('button', { name: /^Mercado/ }).click()
  await expect(page.getByText('Resumo do mês')).toBeVisible()
  await page.getByRole('button', { name: 'Resumo do mês' }).click()
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await expect(lista).toBeVisible()
})

test('trocar de mês com detalhe aberto', async ({ page }) => {
  await abrir(page)
  await page.getByRole('button', { name: /^Saúde/ }).click()
  await expect(page.getByText(/^Saúde · /)).toBeVisible()
  // Saúde existe em maio, abril e março: o detalhe fica aberto e troca de mês.
  for (const rotulo of ['maio de 2026', 'abril de 2026', 'março de 2026']) {
    await anterior(page).click()
    await expect(mes(page, rotulo)).toBeVisible()
    await expect(page.getByText(/^Saúde · /)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Resumo do mês' })).toBeVisible()
  }
  // Em fevereiro de 2026 não há Saúde (dados Show): volta ao resumo.
  await anterior(page).click()
  await expect(mes(page, 'fevereiro de 2026')).toBeVisible()
  await expect(page.getByRole('button', { name: /^Saúde/ })).toHaveCount(0)
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Resumo do mês' })).toHaveCount(0)
})

test('fonte vem do próprio app', async ({ page }) => {
  await abrir(page)
  await page.evaluate(() => document.fonts.ready)
  expect(await page.evaluate(() => document.fonts.check('600 16px Figtree'))).toBe(true)
  const fontes = await page.evaluate(() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family))
  expect(fontes.length).toBeGreaterThan(0)
})

test('erro da API não vira —', async ({ page }) => {
  permitidos.push(/status of 500/)
  await page.route('**/tables/**', r => r.fulfill({ status: 500, body: 'x' }))
  await page.goto('')
  await expect(page.getByText('Não foi possível carregar os dados.')).toBeVisible()
  expect(await page.locator('body').innerText()).not.toContain('—')
  await page.unroute('**/tables/**')
  await page.getByRole('button', { name: 'Tentar de novo' }).click()
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await expect(page.getByText('Não foi possível carregar os dados.')).toHaveCount(0)
})

test('tabela ausente mostra — nos valores dela e nenhuma mensagem de erro', async ({ page }) => {
  permitidos.push(/status of 404/)
  await page.route('**/tables/fact_fatura_mes', r => r.fulfill({ status: 404, body: 'nao existe' }))
  await page.route('**/tables/fact_balance_snapshot', r => r.fulfill({ status: 404, body: 'nao existe' }))
  await abrir(page)
  await expect(faixa(page, 'Fatura pendente')).toHaveText('—')
  await expect(faixa(page, 'Na conta')).toHaveText('—')
  await expect(faixa(page, 'Investido')).toHaveText('R$ 53.212,32')
  await expect(page.getByText('Entrou').first()).toHaveText('Entrou R$ 10.555,36')
  await expect(page.getByText('Não foi possível')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Tentar de novo' })).toHaveCount(0)
})

async function prepararCaptura(page: Page) {
  await abrir(page)
  await page.evaluate(() => document.fonts.ready)
  await page.mouse.move(0, 0)
}

async function comparar(page: Page, chaveRef: string, png: string, testInfo: import('@playwright/test').TestInfo) {
  const atual = await medir(page)
  const r = compararMedidas(atual, referencia[chaveRef], 0.5)
  await testInfo.attach(`medidas-${chaveRef}`, { body: JSON.stringify({ ancoras: r.ancoras, maiorDelta: r.maiorDelta }), contentType: 'application/json' })
  console.log(`${chaveRef}: ${r.ancoras} âncoras comparadas, maior delta ${r.maiorDelta}px`)
  expect(r.diferencas).toEqual([])
  await expect(page).toHaveScreenshot(png, { fullPage: true, maxDiffPixelRatio: 0.001, animations: 'disabled' })
}

test('Resumo bate com o canvas', async ({ page }, testInfo) => {
  await prepararCaptura(page)
  await comparar(page, 'resumo', 'F-resumo.png', testInfo)
})

test('detalhe de Mercado bate com o canvas', async ({ page }, testInfo) => {
  await prepararCaptura(page)
  await page.getByRole('button', { name: /^Mercado/ }).click()
  await expect(page.getByText('Resumo do mês')).toBeVisible()
  await page.mouse.move(0, 0)
  await page.evaluate(() => document.fonts.ready)
  await comparar(page, 'detalheMercado', 'F-detalhe-mercado.png', testInfo)
})
