import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react()],
    server: {
      port: Number(env.VITE_DEV_PORT || 5174),
      host: env.VITE_DEV_HOST || '0.0.0.0',
    },
    preview: {
      port: Number(env.VITE_PREVIEW_PORT || 4174),
      host: env.VITE_PREVIEW_HOST || env.VITE_DEV_HOST || '0.0.0.0',
    },
  }
})
