import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `vite build --mode demo`：展示模式（模擬資料、單一 JS 檔、相對路徑），輸出到 dist-demo
export default defineConfig(({ mode }) => {
  const demo = mode === 'demo'
  return {
    plugins: [react()],
    base: demo ? './' : '/',
    define: demo ? { 'import.meta.env.VITE_DEMO': JSON.stringify('1') } : {},
    server: {
      port: 5173,
      proxy: {
        '/api': { target: 'http://localhost:8000', changeOrigin: true },
      },
    },
    build: {
      outDir: demo ? 'dist-demo' : 'dist',
      assetsDir: 'assets',
      sourcemap: false,
      chunkSizeWarningLimit: 2000,
      rollupOptions: demo ? { output: { inlineDynamicImports: true } } : {},
    },
  }
})
