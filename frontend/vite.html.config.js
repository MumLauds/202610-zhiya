import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: './',
  publicDir: false,
  define: {
    'import.meta.env.VITE_ASSET_BASE': JSON.stringify('../public/'),
  },
  build: {
    outDir: 'html-review',
    emptyOutDir: true,
    rollupOptions: {
      input: 'mobile.html',
    },
  },
})
