import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build into ../frontend/dist (served by server.py).
// Dev server proxies /api and /ws to the FastAPI backend on :8080.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
      '/ws': { target: 'ws://localhost:8080', ws: true },
    },
  },
})
