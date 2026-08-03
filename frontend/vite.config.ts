import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// labeler-engine lives at the repo root, not under frontend/: it is shared with the
// deployed labeler service and must not become frontend code. That puts it outside
// Vite's project root, so it needs both an alias and an fs.allow entry.
const engineDir = fileURLToPath(new URL('../labeler-engine', import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@labeler-engine': engineDir },
  },
  server: {
    fs: { allow: ['.', engineDir] },
  },
})
