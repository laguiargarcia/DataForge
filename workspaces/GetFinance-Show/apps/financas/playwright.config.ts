import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'

const raiz = resolve(import.meta.dirname, '../../../..')
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
    baseURL: `${origem}/apps/GetFinance-Show/financas/`,
    viewport: { width: 1280, height: 980 },
    deviceScaleFactor: 1,
    timezoneId: 'America/Sao_Paulo',
    locale: 'pt-BR',
    browserName: 'chromium',
  },
  projects: [{ name: 'chromium', use: {} }],
  webServer: {
    command: `"${bin}" serve --port ${porta}`,
    url: `${origem}/apps/GetFinance-Show/financas/`,
    env: { DATAFORGE_HOME: raiz },
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
