// Gera e2e/referencia/{F-resumo.png,F-detalhe-mercado.png,medidas.json} a partir do
// artboard publicado. Uso: node e2e/capturar-referencia.mjs <pasta com MixDuplo.dc.html e support.js>
// O artboard carrega a Figtree do Google Fonts; aqui a requisição é interceptada e
// respondida com os mesmos arquivos de fonte do @fontsource que o app usa.
import { chromium } from '@playwright/test'
import { createServer } from 'node:http'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { medir } from './medir.mjs'

const aqui = dirname(fileURLToPath(import.meta.url))
const pasta = resolve(process.argv[2] ?? '')
const fontes = join(aqui, '..', 'node_modules', '@fontsource', 'figtree', 'files')
const pesos = [400, 500, 600, 700]

const servidor = createServer((req, res) => {
  const arq = req.url === '/' ? 'MixDuplo.dc.html' : decodeURIComponent(req.url.slice(1))
  try {
    const corpo = readFileSync(join(pasta, arq))
    res.setHeader('Content-Type', arq.endsWith('.js') ? 'text/javascript' : 'text/html; charset=utf-8')
    res.end(corpo)
  } catch {
    res.statusCode = 404
    res.end()
  }
})
await new Promise(ok => servidor.listen(0, '127.0.0.1', ok))
const origem = `http://127.0.0.1:${servidor.address().port}`

const navegador = await chromium.launch()
const contexto = await navegador.newContext({
  viewport: { width: 1280, height: 980 },
  deviceScaleFactor: 1,
  timezoneId: 'America/Sao_Paulo',
  locale: 'pt-BR',
})
const page = await contexto.newPage()
const externos = []
await page.route(/^https?:\/\/(?!127\.0\.0\.1)/, async rota => {
  const url = rota.request().url()
  if (url.startsWith('https://fonts.googleapis.com/css2')) {
    const css = pesos
      .map(p => `@font-face{font-family:'Figtree';font-style:normal;font-weight:${p};font-display:swap;src:url(https://fonts.gstatic.com/local/figtree-${p}.woff2) format('woff2');}`)
      .join('\n')
    return rota.fulfill({ contentType: 'text/css', body: css })
  }
  const m = /^https:\/\/fonts\.gstatic\.com\/local\/figtree-(\d+)\.woff2$/.exec(url)
  if (m) {
    return rota.fulfill({
      contentType: 'font/woff2',
      body: readFileSync(join(fontes, `figtree-latin-${m[1]}-normal.woff2`)),
    })
  }
  externos.push(url)
  return rota.abort()
})

await page.goto(origem + '/')
await page.getByText('Para onde foi').waitFor()
await page.evaluate(() => document.fonts.ready)
// Só os pesos usados são baixados (400, 500, 600); 700 fica sem uso.
const carregadas = await page.evaluate(() => [...document.fonts].filter(f => f.status === 'loaded').map(f => f.weight).sort())
if (!['400', '500', '600'].every(w => carregadas.includes(w))) throw new Error(`Figtree incompleta na referência: ${carregadas}`)

const destino = join(aqui, 'referencia')
const medidas = {}

await page.mouse.move(0, 0)
medidas.resumo = await medir(page)
await page.screenshot({ path: join(destino, 'F-resumo.png'), fullPage: true, animations: 'disabled' })

await page.getByRole('button', { name: /^Mercado/ }).click()
await page.getByText('Resumo do mês').waitFor()
await page.mouse.move(0, 0)
await page.evaluate(() => document.fonts.ready)
medidas.detalheMercado = await medir(page)
await page.screenshot({ path: join(destino, 'F-detalhe-mercado.png'), fullPage: true, animations: 'disabled' })

writeFileSync(join(destino, 'medidas.json'), JSON.stringify(medidas, null, 1) + '\n')
console.log(`resumo: ${medidas.resumo.length} âncoras; detalhe: ${medidas.detalheMercado.length} âncoras; requisições externas bloqueadas: ${externos.length}`)
for (const u of externos) console.log('  bloqueada:', u)
await navegador.close()
servidor.close()
