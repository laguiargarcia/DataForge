import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// O build vai para dist/, que o servidor do ninho entrega em "/" (ninho/servidor/app.py).
// No `npm run dev`, as rotas vão ao servidor do ninho na porta de docs/commands.md.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  // O logo vem de ninho/identidade, fora desta pasta: o `npm run dev` precisa poder lê-lo.
  server: { proxy: { '/api': 'http://127.0.0.1:8100' }, fs: { allow: ['.', '../identidade'] } },
})
