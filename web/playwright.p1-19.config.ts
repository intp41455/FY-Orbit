import { defineConfig, devices } from '@playwright/test';

// P1-19 专用 E2E 配置：隔离的 outputDir（video/trace 归档到 evidence/p1-19-chat），
// 不复用共享 playwright.config.ts，避免影响其他工单的产物目录。
// 服务器（后端 8101 / dev 5199）由外部脚本启动，这里不配置 webServer。
const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5199';

export default defineConfig({
  testDir: './e2e',
  testMatch: /p1-19-chat\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // 每次运行使用全新输出目录：safe-delete shim 会拦截对已有大目录的批量清理。
  outputDir: process.env.E2E_OUTPUT_DIR ?? 'e2e-results-p1-19-fresh',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: BASE_URL,
    video: 'on',
    screenshot: 'only-on-failure',
  },
  reporter: [['list']],
});
