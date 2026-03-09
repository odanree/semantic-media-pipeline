import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  test: {
    environment: 'node',
    globals: true,
    // Use jsdom for UI/hook test files; keep node for API route tests
    environmentMatchGlobs: [
      ['**/app/__tests__/ui.test.tsx', 'jsdom'],
      ['**/hooks/__tests__/*.{ts,tsx}', 'jsdom'],
    ],
    coverage: {
      provider: 'v8',
      exclude: [
        '**/node_modules/**',
        '**/next.config.*',
        '**/postcss.config.*',
        '**/tailwind.config.*',
        '**/__tests__/**',
        '**/.next/**',
      ],
      // CI fails if coverage drops below these thresholds (mirrors backend --cov-fail-under=77)
      thresholds: {
        statements: 65,
        lines: 65,
        branches: 65,
        functions: 50,
      },
    },
  },
})
