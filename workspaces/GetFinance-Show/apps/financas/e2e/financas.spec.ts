import { test, expect, type Page } from '@playwright/test'


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
  await expect(page.getByRole('button', { name: /^Compras/ })).toBeVisible()
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

// Valores do GetFinance-Show (gerador sintético determinístico), calculados pelo ETL (report_*).
test('junho de 2026 mostra os valores de referência', async ({ page }) => {
  await abrir(page)
  await expect(mes(page, 'junho de 2026')).toBeVisible()
  await expect(page.getByText('Sobrou este mês').locator('..')).toContainText('+R$ 6.066,74')
  await expect(page.getByText('Entrou').first()).toHaveText('Entrou R$ 6.500,00')
  await expect(page.getByText('Saiu', { exact: false }).first()).toHaveText('Saiu R$ 433,26')
  await expect(faixa(page, 'Próxima fatura')).toHaveText('R$ 2.713,73')
  await expect(faixa(page, 'Na conta')).toHaveText('R$ 5.230,55')
  await expect(faixa(page, 'Investido')).toHaveText('R$ 63.943,70')
})

test('trocar de mês atualiza tudo e meses sem saldo mostram —', async ({ page }) => {
  await abrir(page)
  await voltarAte(page, 'maio de 2026')
  await expect(page.getByText('Saiu', { exact: false }).first()).toHaveText('Saiu R$ 4.885,49')
  await expect(faixa(page, 'Próxima fatura')).toHaveText('—')
  await expect(faixa(page, 'Na conta')).toHaveText('—')
  await voltarAte(page, 'dezembro de 2025')
  await expect(page.getByText('Entrou').first()).toHaveText('Entrou R$ 0,00')
  await expect(page.getByText('Saiu', { exact: false }).first()).toHaveText('Saiu R$ 434,62')
  await expect(page.getByText('Sobrou este mês').locator('..')).toContainText('-R$ 434,62')
  await expect(anterior(page)).toBeDisabled()
})

test('não existe mês depois de junho', async ({ page }) => {
  await abrir(page)
  await expect(proximo(page)).toBeDisabled()
  await expect(page.getByText('julho de 2026')).toHaveCount(0)
  await expect(page.getByText('2026-07')).toHaveCount(0)
})

test('lista só as saídas do mês, maior primeiro', async ({ page }) => {
  await abrir(page)
  await voltarAte(page, 'maio de 2026')
  const nomes = await page.locator('button.row span.font-medium').allInnerTexts()
  expect(nomes).toEqual(['Pagamento cartão', 'Assinaturas', 'Transporte', 'Mercado', 'Saúde', 'Compras'])
})

test('drill-down abre, troca e volta', async ({ page }) => {
  await abrir(page)
  await voltarAte(page, 'maio de 2026')
  const lista = page.getByRole('heading', { name: 'Para onde foi' })
  await page.getByRole('button', { name: /^Mercado/ }).click()
  await expect(page.getByText('Mercado · Essencial · 4 transações · 8,4% do que saiu')).toBeVisible()
  await expect(page.getByText('Mercado Central')).toHaveCount(4)
  await expect(page.getByText('Mercado Central').first().locator('..')).toContainText('30/05 · Conta')
  await expect(lista).toBeVisible()
  await page.getByRole('button', { name: /^Saúde/ }).click()
  await expect(page.getByText('Farmacia Sao Joao').first()).toBeVisible()
  await expect(page.getByText('Mercado Central')).toHaveCount(0)
  await page.getByRole('button', { name: /^Saúde/ }).click()
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await page.getByRole('button', { name: /^Mercado/ }).click()
  await page.getByRole('button', { name: 'Resumo do mês' }).click()
  await expect(page.getByText('Sobrou este mês')).toBeVisible()
  await expect(lista).toBeVisible()
})

test('trocar de mês com detalhe aberto', async ({ page }) => {
  await abrir(page)
  await voltarAte(page, 'maio de 2026')
  await page.getByRole('button', { name: /^Saúde/ }).click()
  await expect(page.getByText(/^Saúde · /)).toBeVisible()
  // Saúde existe em abril e março: o detalhe fica aberto e troca de mês.
  for (const rotulo of ['abril de 2026', 'março de 2026']) {
    await anterior(page).click()
    await expect(mes(page, rotulo)).toBeVisible()
    await expect(page.getByText(/^Saúde · /)).toBeVisible()
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
  await page.route('**/tables/report_cartao', r => r.fulfill({ status: 404, body: 'nao existe' }))
  await page.route('**/tables/report_liquidity', r => r.fulfill({ status: 404, body: 'nao existe' }))
  await abrir(page)
  await expect(faixa(page, 'Próxima fatura')).toHaveText('—')
  await expect(faixa(page, 'Na conta')).toHaveText('—')
  await expect(faixa(page, 'Investido')).toHaveText('R$ 63.943,70')
  await expect(page.getByText('Entrou').first()).toHaveText('Entrou R$ 6.500,00')
  await expect(page.getByText('Não foi possível')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Tentar de novo' })).toHaveCount(0)
})

// Regressão visual: as capturas foram geradas da própria tela (2026-11-09, dados do ETL). As
// medidas do canvas da direção F usavam os dados sintéticos antigos e não se aplicam mais.
async function prepararCaptura(page: Page) {
  await abrir(page)
  await page.evaluate(() => document.fonts.ready)
  await page.mouse.move(0, 0)
}

test('Resumo não muda sem querer', async ({ page }) => {
  await prepararCaptura(page)
  await voltarAte(page, 'maio de 2026')
  await expect(page).toHaveScreenshot('resumo-maio.png', { fullPage: true, maxDiffPixelRatio: 0.001, animations: 'disabled' })
})

test('detalhe de Mercado não muda sem querer', async ({ page }) => {
  await prepararCaptura(page)
  await voltarAte(page, 'maio de 2026')
  await page.getByRole('button', { name: /^Mercado/ }).click()
  await expect(page.getByText('Resumo do mês')).toBeVisible()
  await page.mouse.move(0, 0)
  await expect(page).toHaveScreenshot('detalhe-mercado-maio.png', { fullPage: true, maxDiffPixelRatio: 0.001, animations: 'disabled' })
})
