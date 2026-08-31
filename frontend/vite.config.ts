import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置（官方约定文件名）。
// /api 代理到后端 8000：开发期浏览器只访问 5173，同源无跨域（PROJECT-PLAN §7 定稿）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // /health 是基础设施检查，后端不带 /api 前缀；其余 /api/* 原样透传（阶段1 业务路由）
        rewrite: (path) => (path === '/api/health' ? '/health' : path),
      },
    },
  },
})
