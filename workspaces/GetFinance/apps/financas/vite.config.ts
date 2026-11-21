import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const workspace = loadEnv(mode, '.', 'VITE_').VITE_WORKSPACE || 'GetFinance'
  return {
    base: './',
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        // As rotas do backend do app (src/edicao.ts pede `api/...` relativo à página).
        '^/api/(edicao|categorias|regras|correcoes|aplicar)(/|$)': {
          target: 'http://127.0.0.1:8000',
          rewrite: (caminho: string) => caminho.replace(/^\/api/, `/apps/${workspace}/financas/api`),
        },
        '/api': 'http://127.0.0.1:8000',
      },
    },
  }
})
