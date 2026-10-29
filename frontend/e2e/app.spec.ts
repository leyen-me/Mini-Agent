import { expect, test, type Locator, type Page } from '@playwright/test';

/** Pick an option from a Radix (shadcn) Select, which renders its list in a portal. */
async function chooseOption(page: Page, trigger: Locator, name: string) {
  await trigger.click();
  await page.getByRole('option', { name, exact: true }).click();
}

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
  await expect(
    page.locator('.tool-card').filter({ hasText: 'list_dir' }),
  ).toBeVisible();
  await expect(
    // The inspector mirrors each call; assert on the in-chat card.
    page.locator('.tool-card .tool-status').filter({ hasText: '成功' }),
  ).toBeVisible();
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('已收到工具结果，我已完成自检')).toBeVisible();
  await expect(page.locator('.session-item.active')).toContainText('文件');
});

test('permission confirmation allows the shell command once', async ({
  page,
}) => {
  await newSession(page);
  await send(page, '权限');
  await expect(page.getByRole('button', { name: '仅允许一次' })).toBeVisible();
  await expect(page.locator('.interaction-panel')).toContainText(
    'echo permission-check',
  );
  await page.screenshot({ path: 'test-results/permission-prompt.png' });
  await page.getByRole('button', { name: '仅允许一次' }).click();
  await expect(
    page.locator('.tool-card').filter({ hasText: 'echo permission-check' }),
  ).toBeVisible();
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toBeVisible({ timeout: 20_000 });
  await expect(
    // The inspector mirrors each call; assert on the in-chat card.
    page.locator('.tool-card .tool-status').filter({ hasText: '成功' }),
  ).toBeVisible();
});

test('ask_question round trip', async ({ page }) => {
  await newSession(page);
  await send(page, '提问');
  await expect(page.locator('.interaction-header')).toContainText(
    'Agent 需要你的回答',
  );
  await page.getByText('工具调用与权限', { exact: true }).click();
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.getByText(/你的回答是/)).toBeVisible({ timeout: 20_000 });
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toBeVisible();
});

test('queue while streaming and process next turn', async ({ page }) => {
  await newSession(page);
  await send(page, '慢速');
  await expect(page.locator('.streaming-cursor')).toBeVisible({
    timeout: 10_000,
  });
  const input = page.getByTestId('composer-input');
  await input.fill('排队内容');
  await input.press('Enter');
  await expect(page.locator('.queue-panel')).toContainText('排队内容');
  await expect(page.locator('.queue-panel')).toHaveCount(0, {
    timeout: 30_000,
  });
  await expect(
    page.locator('.user-content').filter({ hasText: '排队内容' }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toHaveCount(2, { timeout: 30_000 });
});

test('refresh during streaming resumes without duplicate messages', async ({
  page,
}) => {
  await newSession(page);
  await send(page, '慢速');
  await expect(page.locator('.assistant-text').first()).toBeVisible({
    timeout: 10_000,
  });
  await page.waitForTimeout(700);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(
    page.locator('.user-content').filter({ hasText: '慢速' }),
  ).toHaveCount(1);
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.assistant-panel').first()).toContainText(
    '流式输出',
  );
});

test('goal mode persists objective, criteria and tasks', async ({ page }) => {
  await newSession(page);
  await page.getByRole('button', { name: '更多操作' }).click();
  await page.getByRole('menuitem', { name: '设定目标' }).click();
  await page
    .getByLabel('目标', { exact: true })
    .fill('交付可验收的 mini-agent');
  await page
    .getByLabel('完成标准', { exact: true })
    .fill('附录 D 全部自检通过');
  await page.getByLabel(/任务清单/).fill('实现运行时\n跑通全部测试');
  await page.getByRole('button', { name: '保存目标' }).click();
  await expect(page.locator('.goal-chip')).toContainText('0/2');
  await page.locator('.goal-chip').click();
  const goalDialog = page.getByRole('dialog', { name: '目标模式' });
  await expect(goalDialog.getByRole('textbox').nth(0)).toHaveValue(
    '交付可验收的 mini-agent',
  );
  await expect(goalDialog.getByRole('textbox').nth(1)).toHaveValue(
    '附录 D 全部自检通过',
  );
  await expect(goalDialog.getByRole('textbox').nth(2)).toHaveValue(
    /实现运行时/,
  );
});

test('settings switch language and theme, then restore', async ({ page }) => {
  await newSession(page);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '设置' });
  await expect(dialog).toBeVisible();
  await chooseOption(page, dialog.getByRole('combobox', { name: '界面语言' }), 'English');
  const english = page.getByRole('dialog', { name: 'Settings' });
  await expect(english).toBeVisible();
  await chooseOption(page, english.getByRole('combobox', { name: 'Theme' }), 'Dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: 'test-results/settings-dark.png' });
  await chooseOption(page, english.getByRole('combobox', { name: 'Language' }), '简体中文');
  const chinese = page.getByRole('dialog', { name: '设置' });
  await chooseOption(page, chinese.getByRole('combobox', { name: '主题' }), '跟随系统');
  await chinese.getByRole('button', { name: '关闭' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('read-only share link is created and renders without system prompt', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await newSession(page);
  await send(page, '文件');
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toBeVisible({ timeout: 20_000 });
  const sessionId = await page.evaluate(() =>
    localStorage.getItem('mini-agent-session'),
  );
  expect(sessionId).toBeTruthy();
  await page.getByRole('button', { name: '更多操作' }).click();
  await page.getByRole('menuitem', { name: '分享' }).click();
  await expect(page.getByText(/分享链接已创建/)).toBeVisible({
    timeout: 10_000,
  });
  const shares = await (
    await page.request.get(`/api/v1/sessions/${sessionId}/shares`)
  ).json();
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

test('admin center opens providers and skills', async ({ page }) => {
  await newSession(page);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page
    .getByRole('dialog', { name: '设置' })
    .getByRole('button', { name: '管理中心' })
    .click();
  const admin = page.getByRole('dialog', { name: '管理中心' });
  await expect(admin).toBeVisible();
  await expect(admin).toContainText('离线 Mock 模型');
  await admin.getByRole('tab', { name: 'Skill', exact: true }).click();
  await expect(admin).toContainText('code-review');
  await page.screenshot({ path: 'test-results/admin-skills.png' });
});

test('upload attachment is rendered and sent with the message', async ({
  page,
}) => {
  await newSession(page);
  const input = page.locator('.composer input[type=file]');
  await input.setInputFiles({
    name: 'note.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('attachment from e2e'),
  });
  await expect(page.locator('.attachment-chip')).toContainText('note.txt');
  await send(page, '文件');
  await expect(page.locator('.message-attachments')).toContainText('note.txt', {
    timeout: 20_000,
  });
  await expect(
    page.locator('.turn-status').filter({ hasText: '已完成' }),
  ).toBeVisible({ timeout: 20_000 });
});

test('custom command preview expands arguments before send', async ({
  page,
  request,
}) => {
  const created = await request.post('/api/v1/commands', {
    data: {
      name: 'e2e-review',
      description: 'E2E command',
      promptTemplate: '请处理 $ARGUMENTS',
    },
  });
  expect(created.ok()).toBeTruthy();
  await newSession(page);
  const input = page.getByTestId('composer-input');
  await input.fill('/e2e-review');
  await input.press('Enter');
  const preview = page.getByRole('dialog', { name: '/e2e-review' });
  await expect(preview).toBeVisible();
  await preview.getByRole('textbox').fill('安全 性能');
  await expect(preview.locator('.command-prompt-preview')).toContainText(
    '请处理 安全 性能',
  );
  await preview.getByRole('button', { name: '运行命令' }).click();
  await expect(
    page.locator('.user-content').filter({ hasText: '请处理 安全 性能' }),
  ).toBeVisible();
});

test('workspace file search shows content matches', async ({ page }) => {
  await newSession(page);
  await page.getByRole('tab', { name: '文件' }).click();
  const search = page.locator('.file-search input');
  await search.fill('Mini Agent');
  await expect(page.locator('.search-result').first()).toBeVisible({
    timeout: 10_000,
  });
  await page.locator('.search-result').first().click();
  await expect(page.getByTestId('composer-input')).toHaveValue(/@/);
});

test('workspace switch moves the main view and new session into the target workspace', async ({
  page,
  request,
}) => {
  const created = await request.post('/api/v1/workspaces', {
    data: { name: 'E2E Alternate' },
  });
  expect(created.ok()).toBeTruthy();
  const workspace = (await created.json()).data;
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.composer')).toBeVisible();
  await chooseOption(
    page,
    page.locator('.workspace-select').getByRole('combobox'),
    workspace.alias || workspace.name,
  );
  await expect(page.locator('.setup-card')).toBeVisible();
  await page.locator('.setup-card .primary-button').click();
  await expect(page.locator('.composer')).toBeVisible();
  const sessionId = await page.evaluate(() =>
    localStorage.getItem('mini-agent-session'),
  );
  expect(sessionId).toBeTruthy();
  const snapshot = await (
    await request.get(`/api/v1/sessions/${sessionId}`)
  ).json();
  expect(snapshot.data.session.workspaceId).toBe(workspace.id);
});
