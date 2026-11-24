import { test, expect, type Page } from '@playwright/test'
import { login, soLista } from '../playwright.config'

// O formulário de login (df-8x0m.23.31.5): cada teste começa num navegador sem sessão. Um 401 é
// esperado aqui (é como a tela sabe que ninguém entrou, e o que a senha errada recebe) e o Chromium
// o escreve no console; qualquer outro erro de console ou de página reprova o teste.
let problemas: string[] = []

test.beforeEach(({ page }) => {
  problemas = []
  page.on('console', m => {
    if (m.type() === 'error' && !m.text().includes('401')) problemas.push(`console: ${m.text()}`)
  })
  page.on('pageerror', e => problemas.push(`pageerror: ${e.message}`))
})

test.afterEach(() => {
  expect(problemas).toEqual([])
})

const formulario = (page: Page) => page.getByRole('form', { name: 'Entrar no ninho' })
const barra = (page: Page) => page.getByRole('navigation', { name: 'Módulos' })

async function preencher(page: Page, nome: string, senha: string) {
  await page.getByLabel('Nome').fill(nome)
  await page.getByLabel('Senha').fill(senha)
}

test('sem sessão a página mostra o formulário, que as Senhas do iCloud sabem preencher, e nenhuma tela', async ({ page }) => {
  const pedidos: string[] = []
  page.on('request', r => pedidos.push(new URL(r.url()).pathname))
  await page.goto('')
  await expect(formulario(page)).toBeVisible()
  await expect(page.getByRole('img', { name: 'ninho' })).toBeVisible()
  await expect(barra(page)).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Transações' })).toHaveCount(0)

  const nome = page.getByLabel('Nome')
  const senha = page.getByLabel('Senha')
  await expect(nome).toHaveAttribute('autocomplete', 'username')
  await expect(nome).toHaveAttribute('name', 'username')
  await expect(nome).toHaveAttribute('autocapitalize', 'none')
  await expect(senha).toHaveAttribute('autocomplete', 'current-password')
  await expect(senha).toHaveAttribute('name', 'password')
  await expect(senha).toHaveAttribute('type', 'password')
  // Um campo com letra menor que 16 px faz o Safari do iPhone dar zoom ao tocar.
  for (const campo of [nome, senha]) {
    expect(await campo.evaluate(e => Number.parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(16)
  }
  // Cabe na largura do iPhone, e nada dos módulos foi pedido antes do login.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect(pedidos.filter(p => p.startsWith('/api/') && p !== '/api/eu')).toEqual([])
})

test('nome e senha certos levam à tela da pessoa, e recarregar não pede de novo', async ({ page }) => {
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByLabel('Senha').press('Enter')
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()
  await expect(formulario(page)).toHaveCount(0)
  await expect(barra(page).getByRole('button', { name: 'Finanças', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()
  await expect(formulario(page)).toHaveCount(0)
})

test('o cookie da sessão não é lido pela página', async ({ page, context }) => {
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(barra(page)).toBeVisible()
  expect(await page.evaluate(() => document.cookie)).toBe('')
  const [cookie] = (await context.cookies()).filter(c => c.name === 'ninho_sessao')
  expect([cookie.httpOnly, cookie.secure, cookie.sameSite]).toEqual([true, true, 'Strict'])
})

test('senha errada mostra o erro, limpa a senha e fica no formulário; a certa depois entra', async ({ page }) => {
  await page.goto('')
  await preencher(page, login.nome, 'senha-errada-da-tela')
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText('Nome ou senha errados.')
  await expect(formulario(page)).toBeVisible()
  await expect(page.getByLabel('Nome')).toHaveValue(login.nome)
  await expect(page.getByLabel('Senha')).toHaveValue('')
  await expect(barra(page)).toHaveCount(0)

  await page.getByLabel('Senha').fill(login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('sem rede o formulário diz que não falou com o ninho, e não que a senha está errada', async ({ page }) => {
  await page.goto('')
  await page.route('**/api/entrar', rota => rota.abort())
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Não foi possível falar com o ninho')
  await expect(page.getByLabel('Senha')).toHaveValue(login.senha)
  problemas = []   // o pedido abortado vira um erro de console, esperado aqui
})

test('Sair pede confirmação no segundo toque, volta ao formulário e recarregar segue nele', async ({ page }) => {
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()

  await barra(page).getByRole('button', { name: 'Sair', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()   // um toque só não sai
  await barra(page).getByRole('button', { name: 'Sair?', exact: true }).click()
  await expect(formulario(page)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Transações' })).toHaveCount(0)
  await expect(page.getByLabel('Senha')).toHaveValue('')

  await page.reload()
  await expect(formulario(page)).toBeVisible()
})

test('depois de sair, outra pessoa entra no mesmo navegador e vê só o que é dela', async ({ page }) => {
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair?', exact: true }).click()

  await preencher(page, soLista.nome, soLista.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Lista', exact: true })).toBeVisible()
  await expect(barra(page).getByRole('button', { name: 'Finanças', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Transações' })).toHaveCount(0)
})

test('uma sessão encerrada no servidor leva a tela aberta de volta ao formulário no pedido seguinte', async ({ page, context }) => {
  await page.goto('?tela=lista')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Lista', exact: true })).toBeVisible()

  // Como uma troca de senha ou um 'pessoas sair' faria: a sessão acaba sem a tela saber.
  expect((await context.request.post('api/sair')).status()).toBe(204)
  await page.getByLabel('Novo item').fill('pão')
  await page.getByRole('button', { name: 'Adicionar' }).click()
  await expect(formulario(page)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Lista', exact: true })).toHaveCount(0)
})
