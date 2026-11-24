import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

// O teste de navegador da tela, no tamanho de um iPhone. O alvo é o Safari do iPhone, então o motor
// certo é o WebKit (NAVEGADOR=webkit); nesta máquina ele não roda sem as bibliotecas do sistema
// (sudo), e o padrão é o Chromium com o mesmo tamanho, toque e user agent.
const raiz = resolve(import.meta.dirname, '../..')
const python = process.env.NINHO_PYTHON ?? resolve(raiz, '.venv/bin/python')
const navegador = (process.env.NAVEGADOR ?? 'chromium') as 'chromium' | 'webkit'
const porta = 8766
const senha = 'senha-do-teste-da-tela'   // só do servidor de teste, sobre o dado sintético

export default defineConfig({
  testDir: './e2e',
  testMatch: /\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  timeout: 180_000,
  use: {
    ...devices['iPhone 15'],
    browserName: navegador,
    baseURL: `http://127.0.0.1:${porta}/`,
    httpCredentials: { username: 'lucas', password: senha },
    timezoneId: 'America/Sao_Paulo',
    locale: 'pt-BR',
  },
  projects: [{ name: `iphone-${navegador}`, use: {} }],
  webServer: {
    // Copia o workspace, roda o finance_demo na cópia e sobe o servidor do ninho sobre ela.
    command: `"${python}" e2e/servidor.py ${porta} ${senha}`,
    url: `http://127.0.0.1:${porta}/`,
    reuseExistingServer: false,
    timeout: 180_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
  },
})
