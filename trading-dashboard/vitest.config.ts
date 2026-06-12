import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  test: {
    environment: 'node',
    env: {
      ENCRYPTION_KEY: 'iI+WjblZstJJlVjNt0D1zQpRKrDy1c7UlycDo0himPU=',
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
})
