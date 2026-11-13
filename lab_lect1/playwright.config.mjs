/* Cau hinh Playwright cho cac bai kiem thu giao dien end-to-end. */
import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './test/e2e',
  timeout: 15_000,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:3200', headless: true },
  webServer: {
    command: 'uv run python -m latency_lab.demo',
    url: 'http://127.0.0.1:3200/api/health',
    env: { SERVER_PORT: '3201', PROXY_PORT: '3200', RTT_MS: '0', ALLOW_TEST_RESET: '1' },
    reuseExistingServer: false,
    timeout: 30_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 },
  },
});
