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
  // O texto do botão fica no meio dele.
  const botao = page.getByRole('button', { name: 'Entrar', exact: true })
  expect(await botao.evaluate(e => getComputedStyle(e).textAlign)).toBe('center')
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
  await expect(page.getByLabel('Senha')).toBeFocused()   // o teclado do iPhone segue aberto no campo certo
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
  // O pedido abortado vira um erro de console, esperado aqui; qualquer outro problema segue valendo.
  problemas = problemas.filter(p => !p.includes('net::ERR_FAILED'))
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

test('o "Sair?" volta a "Sair" sozinho, e quem ouve a tela é avisado do segundo toque', async ({ page }) => {
  await page.clock.install()
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair', exact: true }).click()
  const dica = barra(page).getByText('Toque em Sair de novo para confirmar.')
  await expect(dica).toHaveCount(1)
  await expect(dica).toHaveAttribute('aria-live', 'polite')
  await page.clock.fastForward(3900)
  await expect(barra(page).getByRole('button', { name: 'Sair?', exact: true })).toBeVisible()
  await page.clock.fastForward(200)
  await expect(barra(page).getByRole('button', { name: 'Sair', exact: true })).toBeVisible()
  await expect(dica).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()   // ninguém saiu
})

test('sair não leva a tela nem o mês de quem saiu para quem entra depois', async ({ page }) => {
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Desejos', exact: true }).click()
  await expect(page).toHaveURL(/tela=desejos/)
  await barra(page).getByRole('button', { name: 'Sair', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair?', exact: true }).click()
  await expect(formulario(page)).toBeVisible()
  expect(new URL(page.url()).search).toBe('')

  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()   // a primeira tela, não Desejos
  await expect(barra(page).getByRole('button', { name: 'Sair', exact: true })).toBeVisible()   // e não "Sair?"
})

test('quem não tem módulo nenhum vê o aviso e consegue sair', async ({ page }) => {
  await page.goto('')
  await expect(formulario(page)).toBeVisible()
  await page.route('**/api/eu', rota => rota.fulfill({ json: { id: 'p9', nome: 'caio', modulos: [] } }))
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByText('caio, você ainda não tem acesso a nenhuma parte do ninho.')).toBeVisible()
  await page.unroute('**/api/eu')
  const saida = page.waitForRequest(r => new URL(r.url()).pathname === '/api/sair' && r.method() === 'POST')
  await page.getByRole('button', { name: 'Sair', exact: true }).click()
  await saida
  await expect(formulario(page)).toBeVisible()
  await page.reload()
  await expect(formulario(page)).toBeVisible()
})

test('dois envios seguidos fazem um pedido de login só', async ({ page }) => {
  let pedidos = 0
  await page.route('**/api/entrar', async rota => {
    pedidos += 1
    await new Promise(r => setTimeout(r, 300))
    await rota.continue()
  })
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByLabel('Senha').press('Enter')
  await page.getByLabel('Senha').press('Enter')
  await expect(page.getByRole('heading', { name: 'Transações' })).toBeVisible()
  expect(pedidos).toBe(1)
})

test('depois de sair com um aplicar em curso, nada mais pergunta por ele ao servidor', async ({ page }) => {
  // Um aplicar guardado no aparelho, que o servidor diz estar rodando: a tela de finanças o acompanha.
  await page.addInitScript(() => localStorage.setItem('ninho.aplicar.run_id', '00000000-0000-4000-8000-000000000001'))
  let andamentos = 0
  await page.route('**/api/financas/aplicar/**', rota => {
    andamentos += 1
    return rota.fulfill({ json: { run_id: '00000000-0000-4000-8000-000000000001', state: 'running', finished_at: null } })
  })
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByText('Aplicando: recalculando as categorias…')).toBeVisible()
  await barra(page).getByRole('button', { name: 'Sair', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair?', exact: true }).click()
  await expect(formulario(page)).toBeVisible()
  const aoSair = andamentos
  // A rede sossega: com alguém ainda acompanhando, cada resposta puxaria outro pedido e ela nunca pararia.
  await page.waitForLoadState('networkidle')
  expect(andamentos).toBeLessThanOrEqual(aoSair + 1)   // no máximo o pedido que já estava em voo
})

test('a barra com o Sair cabe num iPhone estreito (320 px)', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 })
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(barra(page).getByRole('button')).toHaveText(['Finanças', 'Compras', 'Desejos', 'Sair'])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const sair = await barra(page).getByRole('button', { name: 'Sair', exact: true }).boundingBox()
  expect(sair!.x + sair!.width).toBeLessThanOrEqual(320)
  expect(sair!.width).toBeGreaterThanOrEqual(44)
})

test('depois de sair, outra pessoa entra no mesmo navegador e vê só o que é dela', async ({ page }) => {
  await page.goto('')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair', exact: true }).click()
  await barra(page).getByRole('button', { name: 'Sair?', exact: true }).click()

  await preencher(page, soLista.nome, soLista.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Compras', exact: true })).toBeVisible()
  await expect(barra(page).getByRole('button', { name: 'Finanças', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Transações' })).toHaveCount(0)
})

test('uma sessão encerrada no servidor leva a tela aberta de volta ao formulário no pedido seguinte', async ({ page, context }) => {
  await page.goto('?tela=lista')
  await preencher(page, login.nome, login.senha)
  await page.getByRole('button', { name: 'Entrar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Compras', exact: true })).toBeVisible()

  // Como uma troca de senha ou um 'pessoas sair' faria: a sessão acaba sem a tela saber.
  expect((await context.request.post('api/sair')).status()).toBe(204)
  await page.getByLabel('Novo item').fill('pão')
  await page.getByRole('button', { name: 'Adicionar' }).click()
  await expect(formulario(page)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Compras', exact: true })).toHaveCount(0)
})
