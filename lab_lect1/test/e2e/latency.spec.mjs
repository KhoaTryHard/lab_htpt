/* Test browser behavior against the Python backend and RTT proxy. */
import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';

const base = {
  fullName: 'Nguyễn Văn A',
  email: 'broken',
  password: 'password1',
  confirmPassword: 'password1'
};

test.beforeAll(async ({ request }) => {
  // UI mode initializes webServer asynchronously; an early Run all must wait for it.
  test.setTimeout(30_000);
  await expect.poll(async () => {
    try {
      return (await request.get('/api/health', { timeout: 1000 })).ok();
    } catch {
      return false;
    }
  }, { timeout: 20_000, message: 'Waiting for the Python server and proxy' }).toBe(true);
});

async function fill(page, value) {
  for (const [field, text] of Object.entries(value)) {
    await page.locator(`[data-field="${field}"]`).fill(text);
  }
}

for (const mode of ['server', 'client']) {
  test(`${mode}: malformed data has the expected latency and request count`, async ({ page }) => {
    await page.goto('/?rtt=500');
    await page.locator(`input[value="${mode}"]`).check();
    await fill(page, base);
    const requests = [];
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/register') requests.push(request);
    });
    const expected = mode === 'client' ? 'client-validation-error' : 'server-error';
    await page.locator('#submit-button').click();
    await expect(page.locator('#result')).toHaveAttribute('data-outcome', expected);
    await expect(page.locator('[data-error-for="email"]')).toContainText('định dạng');
    const result = await page.evaluate(() => window.__lab.last);
    if (mode === 'client') {
      expect(result.feedbackMs).toBeLessThan(250);
      await page.waitForTimeout(300);
      expect(requests).toHaveLength(0);
    } else {
      expect(result.feedbackMs).toBeGreaterThanOrEqual(450);
      expect(result.status).toBe(422);
      expect(requests).toHaveLength(1);
    }
  });

  test(`${mode}: a new email succeeds and resubmitting it detects the duplicate`, async ({ page }) => {
    await page.goto('/?rtt=200');
    await page.locator(`input[value="${mode}"]`).check();
    await fill(page, { ...base, email: `e2e-${randomUUID()}@example.test` });
    await page.locator('#submit-button').click();
    await expect(page.locator('#result')).toHaveAttribute('data-outcome', 'success');
    expect(await page.evaluate(() => window.__lab.last.status)).toBe(201);
    await page.locator('#submit-button').click();
    await expect(page.locator('#result')).toHaveAttribute('data-outcome', 'server-error');
    expect(await page.evaluate(() => window.__lab.last.status)).toBe(409);
    await expect(page.locator('#result')).toContainText('tồn tại');
  });

  test(`${mode}: the seeded duplicate always needs the server`, async ({ page }) => {
    await page.goto('/?rtt=500');
    await page.locator(`input[value="${mode}"]`).check();
    await fill(page, { ...base, email: 'taken@example.test' });
    const response = page.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/register');
    await page.locator('#submit-button').click();
    expect((await response).status()).toBe(409);
    await expect(page.locator('#result')).toHaveAttribute('data-outcome', 'server-error');
    expect(await page.evaluate(() => window.__lab.last.feedbackMs)).toBeGreaterThanOrEqual(450);
  });
}

test('network failure shows a message and enables resubmission', async ({ page }) => {
  await page.goto('/');
  await fill(page, { ...base, email: `network-${randomUUID()}@example.test` });
  await page.route('**/api/register', (route) => route.abort());
  await page.locator('#submit-button').click();
  await expect(page.locator('#result')).toHaveAttribute('data-outcome', 'network-error');
  await expect(page.locator('#result')).toContainText('Không thể kết nối');
  await expect(page.locator('#submit-button')).toBeEnabled();
});
