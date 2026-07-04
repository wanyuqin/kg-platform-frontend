import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 本地联调：控制台接口与 Gateway 都代理到后端 api 进程
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/v1': 'http://localhost:8000',
    },
  },
})
