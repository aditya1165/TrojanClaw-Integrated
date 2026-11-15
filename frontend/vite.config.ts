import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  server: {
    headers: { 'Referrer-Policy': 'no-referrer' },
    allowedHosts: ['antelope-barricade-bluish.ngrok-free.dev'],
    proxy: {
      '/auth/google/callback': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/mcp': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/.well-known': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/authorize': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/token': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/register': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/revoke': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api(?=\/|$)/, ''),
        // Google callbacks use /auth/google directly on the backend. Keep its
        // HttpOnly attempt cookie usable across that callback and /api requests.
        cookiePathRewrite: { '/auth/google': '/' },
      },
    },
  },
  plugins: [react(), tailwindcss()],
})
