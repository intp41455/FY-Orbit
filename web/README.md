# Find Yourself — Web (React + TypeScript + Vite PWA)

Web 前端切片（`web/`）。不要从本目录修改 Python 源码、迁移、infra 或 CLI。

## Commands
```powershell
npm ci              # locked install from package-lock.json
npm run typecheck   # tsc --noEmit
npm run build       # tsc --noEmit && vite build (emits dist/ + sw.js)
npm run test        # vitest unit/component tests (jsdom, mocks isolated)
npm run test:e2e    # playwright（需先启动后端服务）
```

## Architecture
- `src/api/` — typed API client + data models for every contract endpoint:
  auth, conversations/messages, tasks/SSE, memory search, proposals/decision,
  assessments, agents/skills, artifacts, export, settings.
- `src/pages/` — Login, Chat, History, Growth, Assessments, Workbench,
  Approvals, Skills(capability catalog), Private space, Settings.
- All data comes from the real backend; no demo success is faked when the
  service/credentials are missing.

## PWA 隐私
- The service worker (`vite-plugin-pwa` generateSW) precaches ONLY static shell
  assets. `runtimeCaching` is intentionally empty; `/api`, `/auth`, `/health`,
  `/metrics` are excluded from the SPA navigation fallback.
- No API responses, private chat, export/download links, or tokens are cached.
- Tokens live in HttpOnly cookies; JS never reads them.
- Offline: composer disabled, explicit "offline, cannot submit" notice.
- Logout clears localStorage/sessionStorage and all Cache Storage.

## 测评合规
- Scoring is server-side; missing answers never produce a default result.
- Big Five uses IPIP entries (compliance notice rendered verbatim, no percentile
  without a norm); four-dimension is labelled exploratory/non-official MBTI;
  Enneagram and custom composites are exploratory and never claimed clinically
  validated.

## Status
本地 build / typecheck / 单元测试通过；真实 E2E 需先启动后端服务。
