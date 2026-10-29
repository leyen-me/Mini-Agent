import { expect, test, type Page } from '@playwright/test';

async function newSession(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.composer')).toBeVisible();
  const cards = page.locator('.prompt-card');
  if (await cards.count()) {
    // No-op: empty session is ready.
  }
  const sessionItems = page.locator('.session-item');
  await expect(sessionItems.first()).toBeVisible();
  await page.locator('.new-session-button').click();
  await expect(page.locator('.composer')).toBeVisible();
  await page.waitForTimeout(400);
}

async function send(page: Page, text: string) {
  const input = page.getByTestId('composer-input');
  await input.fill(text);
  await input.press('Enter');
}

test('basic chat, file tool and final answer', async ({ page }) => {
  await newSession(page);
  await send(page, '文件');
  await expect(page.locator('.tool-card').filter({ hasText: 'list_dir' })).toBeVisible();
  await expect(page.locator('.tool-status').filter({ hasText: '成功' })).toBeVisible();
  await expect(page.locator('.turn-status').filter({ hasText: '已完成' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('已收到工具结果，我已完成自检')).toBeVisible();
  await expect(page.locator('.session-item.active')).toContainText('文件');
});

test('permission confirmation allows the shell command once', async ({ page }) => {
  await newSession(page);
  await send(page, '权限');
  await expect(page.getByRole('button', { name: '仅允许一次' })).toBeVisible();
  await expect(page.locator('.interaction-panel')).toContainText('echo permission-check');
  await page.screenshot({ path: 'test-results/permission-prompt.png' });
  await page.getByRole('button', { name: '仅允许一次' }).click();
  await expect(page.locator('.tool-card').filter({ hasText: 'echo permission-check' })).toBeVisible();
  await expect(page.locator('.turn-status').filter({ hasText: '已完成' })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.tool-status').filter({ hasText: '成功' })).toBeVisible();
});

test('ask_question round trip', async ({ page }) => {
  await newSession(page);
  await send(page, '提问');
  await expect(page.locator('.interaction-header')).toContainText('Agent 需要你的回答');
  await page.getByText('工具调用与权限', { exact: true }).click();
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.getByText(/你的回答是/)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('.turn-status').filter({ hasText: '已完成' })).toBeVisible();
});

test('queue while streaming and process next turn', async ({ page }) => {
  await newSession(page);
  await send(page, '慢速');
  await expect(page.locator('.streaming-cursor')).toBeVisible({ timeout: 10_000 });
  const input = page.getByTestId('composer-input');
  await input.fill('排队内容');
  await input.press('Enter');
  await expect(page.locator('.queue-panel')).toContainText('排队内容');
  await expect(page.locator('.queue-panel')).toHaveCount(0, { timeout: 30_000 });
  await expect(page.locator('.user-content').filter({ hasText: '排队内容' })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.turn-status').filter({ hasText: '已完成' })).toHaveCount(2, { timeout: 30_000 });
});

test('refresh during streaming resumes without duplicate messages', async ({ page }) => {
  await newSession(page);
  await send(page, '慢速');
  await expect(page.locator('.assistant-text').first()).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(700);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('.user-content').filter({ hasText: '慢速' })).toHaveCount(1);
  await expect(page.locator('.turn-status').filter({ hasText: '已完成' })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.assistant-panel').first()).toContainText('流式输出');
});

test('goal mode persists objective, criteria and tasks', async ({ page }) => {
  await newSession(page);
  await page.getByRole('button', { name: '更多操作' }).click();
  await page.getByRole('menuitem', { name: '设定目标' }).click();
  await page.getByLabel('目标', { exact: true }).fill('交付可验收的 mini-agent');
  await page.getByLabel('完成标准', { exact: true }).fill('附录 D 全部自检通过');
  await page.getByLabel(/任务清单/).fill('实现运行时\n跑通全部测试');
  await page.getByRole('button', { name: '保存目标' }).click();
  await expect(page.locator('.goal-chip')).toContainText('0/2');
  await page.locator('.goal-chip').click();
  const goalDialog = page.getByRole('dialog', { name: '目标模式' });
  await expect(goalDialog.getByRole('textbox').nth(0)).toHaveValue('交付可验收的 mini-agent');
  await expect(goalDialog.getByRole('textbox').nth(1)).toHaveValue('附录 D 全部自检通过');
  await expect(goalDialog.getByRole('textbox').nth(2)).toHaveValue(/实现运行时/);
});

test('settings switch language and theme, then restore', async ({ page }) => {
  await newSession(page);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '设置' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('combobox').nth(0).selectOption('en-US');
  const english = page.getByRole('dialog', { name: 'Settings' });
  await expect(english).toBeVisible();
  await english.getByRole('combobox').nth(1).selectOption('dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: 'test-results/settings-dark.png' });
  await english.getByRole('combobox').nth(0).selectOption('zh-CN');
  const chinese = page.getByRole('dialog', { name: '设置' });
  await chinese.getByRole('combobox').nth(1).selectOption('system');
  await chinese.getByRole('button', { name: '关闭' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('read-only share link is created and renders without system prompt', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await newSession(page);
  await send(page, '文件');
  await expect(page.locator('.turn-status').filter({ hasText: '已完成' })).toBeVisible({ timeout: 20_000 });
  const sessionId = await page.evaluate(() => localStorage.getItem('mini-agent-session'));
  expect(sessionId).toBeTruthy();
  await page.getByRole('button', { name: '更多操作' }).click();
  await page.getByRole('menuitem', { name: '分享' }).click();
  await expect(page.getByText(/分享链接已创建/)).toBeVisible({ timeout: 10_000 });
  const shares = await (await page.request.get(`/api/v1/sessions/${sessionId}/shares`)).json();
  expect(shares.data.length).toBeGreaterThan(0);
  const token = shares.data[0].token;
  const shared = await context.newPage();
  await shared.goto(`/share/${token}`, { waitUntil: 'domcontentloaded' });
  await expect(shared.getByText('只读分享').first()).toBeVisible();
  await expect(shared.getByText('已收到工具结果，我已完成自检')).toBeVisible();
  const body = await shared.locator('body').innerText();
  expect(body).not.toContain('system prompt');
  expect(body).not.toContain('sk-');
});
