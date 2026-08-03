import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时 /api 代理到本机 FastAPI（uvicorn，端口 8000）
// 生产构建产物输出到 frontend/dist/，由 backend/api/server.py 同源托管
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../frontend/dist',
    emptyOutDir: true,
  },
})
