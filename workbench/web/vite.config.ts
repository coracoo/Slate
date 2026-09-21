import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const backend = 'http://127.0.0.1:8775'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/media': backend,
      // 后端媒体流端点是精确的 /src?p=...；Vite 自身源码也在 /src/ 下，
      // 必须用 bypass 排除，否则 dev 下 main.ts 等会被代理劫走。
      '/src': {
        target: backend,
        bypass(req) {
          const path = (req.url || '').split('?')[0]
          return /^\/src\/?$/.test(path) ? undefined : (req.url as string)
        }
      }
    }
  }
})
