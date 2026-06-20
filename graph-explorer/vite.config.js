import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/graph':      'http://graph-api:4003',
      '/ingest':     'http://graph-api:4003',
      '/quality':    'http://graph-api:4003',
      '/templates':  'http://graph-api:4003',
      '/schemas':    'http://graph-api:4003',
      '/reconcile':  'http://graph-api:4003',
      '/bills':      'http://graph-api:4003',
    },
  },
  build: {
    outDir: '../graph-api/explorer-dist',
    emptyOutDir: true,
  },
})
