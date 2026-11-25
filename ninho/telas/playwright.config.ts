import { defineConfig, devices } from '@playwright/test'
import { resolve } from 'node:path'

// O teste de navegador da tela, no tamanho de um iPhone. O alvo é o Safari do iPhone, então o motor
// certo é o WebKit (NAVEGADOR=webkit); nesta máquina ele não roda sem as bibliotecas do sistema
// (sudo), e o padrão é o Chromium com o mesmo tamanho, toque e user agent.
const raiz = resolve(import.meta.dirname, '../..')
const python = process.env.NINHO_PYTHON ?? resolve(raiz, '.venv/bin/python')
const navegador = (process.env.NAVEGADOR ?? 'chromium') as 'chromium' | 'webkit'
// A porta pode vir de fora: duas sessões rodando o e2e ao mesmo tempo não cabem na mesma.
const porta = Number(process.env.NINHO_E2E_PORTA ?? 8766)
// A pessoa que o esqueleto cria no banco do app da cópia: só do servidor de teste, sobre o dado sintético.
export const login = { nome: 'ana', senha: 'senha-falsa-do-teste-da-tela' }
// Uma segunda pessoa, só com a lista: o teste da barra entra como ela (e2e/tela.spec.ts).
export const soLista = { nome: 'beto', senha: 'outra-senha-falsa-da-tela' }

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
    timezoneId: 'America/Sao_Paulo',
    locale: 'pt-BR',
  },
  projects: [{ name: `iphone-${navegador}`, use: {} }],
  webServer: {
    // O comando único do esqueleto: copia o workspace, roda o finance_demo na cópia e sobe o
    // servidor do ninho sobre ela; a cópia some quando o teste encerra o servidor.
    command: `"${python}" "${resolve(raiz, 'scripts/dev/esqueleto.py')}" --porta ${porta}`,
    env: {
      NINHO_ESQUELETO_NOME: login.nome, NINHO_ESQUELETO_SENHA: login.senha,
      NINHO_ESQUELETO_NOME_LISTA: soLista.nome, NINHO_ESQUELETO_SENHA_LISTA: soLista.senha,
    },
    url: `http://127.0.0.1:${porta}/`,
    reuseExistingServer: false,
    timeout: 180_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
  },
})
