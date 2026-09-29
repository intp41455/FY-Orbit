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
// Build a same-origin proxy for /api /auth /health /metrics that forwards to
// the real backend. The browser stays on one origin (cookies + CSRF work without
// CORS). The backend enforces a trusted-origin check on writes, so we rewrite
// the outbound Origin header to the target origin. Loopback/container plumbing.
function makeBackendProxy(target: string) {
  const opts = {
    target,
    changeOrigin: true,
    configure: (proxy: {
      on: (ev: string, cb: (proxyReq: { removeHeader: (k: string) => void; setHeader: (k: string, v: string) => void }) => void) => void;
    }) => {
      proxy.on('proxyReq', (proxyReq) => {
        proxyReq.removeHeader('Origin');
        proxyReq.setHeader('Origin', target);
      });
    },
  };
  return { '/api': opts, '/auth': opts, '/health': opts, '/metrics': opts };
}

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
  // Dev server. Same loopback bind as before, plus a same-origin proxy to the
  // backend. In a container set VITE_DEV_API_TARGET=http://api:8000; the browser
  // still talks to the dev origin and sees no CORS.
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: makeBackendProxy(process.env.VITE_DEV_API_TARGET ?? 'http://127.0.0.1:8030'),
  },
  // Production preview used by Playwright E2E. Bind loopback only, and proxy
  // API/auth/health/metrics to the real backend so the browser sees a single
  // same-origin origin (cookies + CSRF work without CORS). Target is injected
  // via E2E_API_TARGET (e.g. http://127.0.0.1:8030).
  preview: {
    host: '127.0.0.1',
    port: Number(process.env.E2E_PREVIEW_PORT ?? 4173),
    strictPort: true,
    proxy: makeBackendProxy(process.env.E2E_API_TARGET ?? 'http://127.0.0.1:8030'),
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
