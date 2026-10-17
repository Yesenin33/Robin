import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: /api/* -> FastAPI on :8000 (same-origin, no CORS needed). Prod: nginx does the same.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: p => p.replace(/^\/api/, '') },
    },
  },
})
