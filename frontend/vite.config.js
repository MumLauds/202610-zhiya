import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { configDefaults } from 'vitest/config'
import { cp } from 'node:fs/promises'
import { resolve } from 'node:path'

export default defineConfig(({ mode }) => ({
  // Public URLs are served by Vite; jsdom must not import them as Windows files.
  plugins: [vue(mode === 'test' ? { template: { transformAssetUrls: { includeAbsolute: false } } } : {}), mode === 'public' && {
    name: 'public-site-assets',
    async writeBundle({ dir }) {
      for (const name of ['assets', 'vendor']) {
        await cp(resolve('public', name), resolve(dir, name), { recursive: true })
      }
    },
  }],
  resolve: { alias: mode === 'public' ? { './data/demo-books.json': resolve('mobile/data/empty-books.json') } : {} },
  test: {
    environment: 'jsdom',
    clearMocks: true,
    restoreMocks: true,
    exclude: [...configDefaults.exclude, 'tests/readingSessionMachine.test.js', 'tests/mobileWriteRecovery.test.js'],
  },
  build: {
    copyPublicDir: mode !== 'public',
    rollupOptions: {
      input: ['index.html', 'mobile.html'],
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
}))
