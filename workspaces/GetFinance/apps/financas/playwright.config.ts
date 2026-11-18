import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const raiz = resolve(import.meta.dirname, '../../../..')
// O e2e e as capturas só rodam sobre o dado sintético do finance_demo, nunca sobre dado real.
const marca = resolve(import.meta.dirname, '../../data/raw/SYNTHETIC')
if (!existsSync(marca)) {
  throw new Error(`Sem ${marca}: rode antes "dataforge run GetFinance finance_demo" (dado sintético).`)
}
const bin = process.env.DATAFORGE_BIN ?? resolve(raiz, '.venv/bin/dataforge')
const porta = 8765
const origem = `http://127.0.0.1:${porta}`

export default defineConfig({
  testDir: './e2e',
  testMatch: /\.spec\.ts/,
  snapshotPathTemplate: '{testDir}/referencia/{arg}{ext}',
  updateSnapshots: 'none',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: `${origem}/apps/GetFinance/financas/`,
    viewport: { width: 1280, height: 980 },
    deviceScaleFactor: 1,
    timezoneId: 'America/Sao_Paulo',
    locale: 'pt-BR',
    browserName: 'chromium',
  },
  projects: [{ name: 'chromium', use: {} }],
  webServer: {
    command: `"${bin}" serve --port ${porta}`,
    url: `${origem}/apps/GetFinance/financas/`,
    env: { DATAFORGE_HOME: raiz },
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
