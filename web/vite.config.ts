/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

// Find Yourself Web slice — Vite config.
// Privacy rule (FROZEN_CONTRACT §11): the service worker may ONLY precache the
// static app shell. It must NOT cache API responses, private chat messages,
// export/download links, or auth tokens. We therefore register NO runtimeCaching
// rules for /api or /auth, and we denylist those paths from the SPA navigation
// fallback so the network is always consulted for them.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      filename: 'sw.js',
      manifest: {
        name: 'Find Yourself',
        short_name: 'FindYourself',
        description: 'Single-owner self-exploration companion PWA',
        theme_color: '#0f172a',
        background_color: '#0f172a',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
        ],
      },
      workbox: {
        // Precache only hashed static shell assets produced by the build.
        globPatterns: ['**/*.{js,css,html,svg,webmanifest,ico}'],
        // SPA fallback for app routes, but never for API/auth/health.
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/auth\//, /^\/health\//, /^\/metrics\//],
        // Explicitly empty: we deliberately do NOT add runtime caching for
        // API, private messages, exports, downloads, or tokens.
        runtimeCaching: [],
      },
      devOptions: {
        // Keep the SW disabled during `vite dev` so offline/privacy behavior is
        // only exercised on the production build that ships the real sw.js.
        enabled: false,
      },
    }),
  ],
  server: {
    port: 5173,
    host: '127.0.0.1',
  },
  build: {
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: false,
    css: false,
    restoreMocks: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**'],
  },
});
