import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { configDefaults } from 'vitest/config'

export default defineConfig(({ mode }) => ({
  // Public URLs are served by Vite; jsdom must not import them as Windows files.
  plugins: [vue(mode === 'test' ? { template: { transformAssetUrls: { includeAbsolute: false } } } : {})],
  test: {
    environment: 'jsdom',
    clearMocks: true,
    restoreMocks: true,
    exclude: [...configDefaults.exclude, 'tests/readingSessionMachine.test.js', 'tests/mobileWriteRecovery.test.js'],
  },
  build: {
    rollupOptions: {
      input: ['index.html', 'mobile.html'],
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
}))
