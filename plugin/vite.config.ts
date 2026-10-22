import { defineConfig } from 'vite'

// Loopback by default. For a phone sideload, set GATE_DEV_HOST to your tailnet IP
// (`tailscale ip -4`) rather than exposing the dev server on every interface.
export default defineConfig({
  server: {
    host: process.env.GATE_DEV_HOST ?? '127.0.0.1',
    port: 5180,
    strictPort: true,
    fs: { allow: ['.', '../web'] }, // the phone page shares ../web with the relay's desktop page
  },
  build: { target: 'esnext' },
})
