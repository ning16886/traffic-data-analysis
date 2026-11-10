import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 页面里只写 /api/xxx，由 dev server 转发到后端，避免把后端地址写死在代码里
      // 后端跑在 Docker 里时，把 target 改成 http://backend:8000
      // target 必须写 127.0.0.1 而不是 localhost：Windows 上 localhost 先解析成
      // IPv6 的 ::1，而 uvicorn 默认只监听 IPv4，写 localhost 代理会连不上后端。
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
