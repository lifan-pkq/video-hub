import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          icons: ['@phosphor-icons/react'],
          timeline: ['@xzdarcy/react-timeline-editor'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.VIDEO_HUB_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/narrato': {
        target: 'http://127.0.0.1:8501',
        changeOrigin: true,
        ws: true,
      },
      '/preview': {
        target: 'http://127.0.0.1:8502',
        changeOrigin: true,
      },
    },
  },
})
