# Find Yourself — Web (React + TypeScript + Vite PWA)

Web slice per `docs/FROZEN_CONTRACT.md` v1.0. Scope: `web/` only. Do not modify
Python sources, migrations, infra, or CLI from here.

## Commands
```powershell
npm ci              # locked install from package-lock.json
npm run typecheck   # tsc --noEmit
npm run build       # tsc --noEmit && vite build (emits dist/ + sw.js)
npm run test        # vitest unit/component tests (jsdom, mocks isolated)
npm run test:e2e    # playwright (BLOCKED_EXTERNAL until backend is up)
```

## Architecture
- `src/api/` — typed API client + data models for every contract endpoint:
  auth, conversations/messages, tasks/SSE, memory search, proposals/decision,
  assessments, agents/skills, artifacts, export, settings.
- `src/pages/` — Login, Chat, History, Growth, Assessments, Workbench,
  Approvals, Skills(capability catalog), Private space, Settings.
- All data comes from the real backend; no demo success is faked when the
  service/credentials are missing.

## PWA privacy (FROZEN_CONTRACT §11)
- The service worker (`vite-plugin-pwa` generateSW) precaches ONLY static shell
  assets. `runtimeCaching` is intentionally empty; `/api`, `/auth`, `/health`,
  `/metrics` are excluded from the SPA navigation fallback.
- No API responses, private chat, export/download links, or tokens are cached.
- Tokens live in HttpOnly cookies; JS never reads them.
- Offline: composer disabled, explicit "offline, cannot submit" notice.
- Logout clears localStorage/sessionStorage and all Cache Storage.

## Assessment compliance (§11.3)
- Scoring is server-side; missing answers never produce a default result.
- Big Five uses IPIP entries (compliance notice rendered verbatim, no percentile
  without a norm); four-dimension is labelled exploratory/non-official MBTI;
  Enneagram and custom composites are exploratory and never claimed clinically
  validated.

## Status
Local build/typecheck/unit tests pass. Real E2E against the backend is
BLOCKED_EXTERNAL (Runtime slice not deployed). See `evidence/g5/`.
