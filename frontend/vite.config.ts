import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  server: {
    proxy: {
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
