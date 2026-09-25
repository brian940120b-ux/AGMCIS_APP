import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The backend runs on :8080 — not :8000, which the trading system in this same
// repository uses. During development the dev server proxies /api so the
// frontend uses same-origin relative URLs in every environment.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Mirrors the "@/*" path alias declared in tsconfig.app.json.
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8080', ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
