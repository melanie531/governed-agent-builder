import {test, expect} from '@playwright/test';

test('administrator discovers, validates, approves and publishes a model', async ({page}) => {
  await page.request.post('/api/demo/session', {data: {persona_id: 'admin'}, headers: {origin: 'http://127.0.0.1:5189'}});
  await page.goto('/');
  await expect(page.getByRole('heading', {name: 'Platform overview', exact: true})).toBeVisible();
  await page.getByRole('link', {name: 'Registry & AI Catalog', exact: true}).click();
  await page.getByRole('button', {name: 'Discover Bedrock models'}).click();
  await page.getByRole('button', {name: 'Discovered Bedrock model'}).click();
  await page.getByRole('option', {name: /Synthetic model/}).click();
  await page.getByRole('button', {name: 'Model workspaces'}).click();
  await page.getByRole('option', {name: 'research', exact: true}).click();
  await page.keyboard.press('Escape');
  await page.getByRole('textbox', {name: 'Model registration reason'}).fill('Approved synthetic model test');
  await page.getByRole('button', {name: 'Register model', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Synthetic model', exact: true})).toBeVisible();
  await page.getByRole('textbox', {name: 'Catalog decision reason'}).fill('Reviewed model for research use');
  await expect(page.getByRole('button', {name: 'Publish to AI Catalog'})).toHaveCount(0);
  await page.getByRole('button', {name: 'Validate model connection'}).click();
  await expect(page.getByText('Model connection validated.', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Register in AgentCore'}).click();
  await page.getByRole('button', {name: 'Submit for approval'}).click();
  await page.getByRole('button', {name: 'Approve Registry record'}).click();
  await page.getByRole('button', {name: 'Publish to AI Catalog'}).click();
  await expect(page.getByText('Catalog version published. Users request access from the AI Catalog.', {exact: true})).toBeVisible();
  await expect(page.getByRole('row').filter({hasText: 'Synthetic model'}).getByText('Published', {exact: true})).toBeVisible();
  await page.getByRole('link', {name: 'Performance', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Model performance'})).toBeVisible();
  await expect(page.getByRole('cell', {name: '0.80 s', exact: true}).first()).toBeVisible();
  await page.getByRole('link', {name: 'Platform cost', exact: true}).click();
  await expect(page.getByText('Cost data unavailable', {exact: true})).toBeVisible();
  await expect(page.getByText('$0.00', {exact: true})).toHaveCount(0);
});

test('tool request status updates while the business user stays on the page', async ({browser}) => {
  const business = await browser.newContext();
  const admin = await browser.newContext();
  try {
    const userPage = await business.newPage(), adminPage = await admin.newPage();
    for (const [page, persona_id] of [[userPage, 'alex'], [adminPage, 'admin']] as const) {
      await page.request.post('/api/demo/session', {data: {persona_id}, headers: {origin: 'http://127.0.0.1:5189'}});
      await page.goto('/');
    }
    await userPage.getByRole('link', {name: 'Tool requests', exact: true}).click();
    await userPage.getByRole('textbox', {name: 'What tool do you need?'}).fill('S3');
    await userPage.getByRole('button', {name: 'Send request', exact: true}).click();
    await expect(userPage.getByText('Tool request sent.', {exact: true})).toBeVisible();
    await adminPage.getByRole('link', {name: 'Policies & approvals', exact: true}).click();
    const row = adminPage.getByRole('row').filter({has: adminPage.getByRole('cell', {name: 'S3', exact: true})});
    await row.getByRole('textbox', {name: 'Response for S3', exact: true}).fill('Reviewing storage connector requirements.');
    await row.getByRole('button', {name: 'Save response', exact: true}).click();
    await expect(userPage.getByText('Reviewing storage connector requirements.', {exact: true})).toBeVisible({timeout: 20000});
    await expect(userPage.getByRole('row').filter({has: userPage.getByRole('cell', {name: 'S3', exact: true})}).getByText('In review', {exact: true})).toBeVisible();
  } finally {
    await business.close(); await admin.close();
  }
});

test('an administrator draft cannot overwrite a newer response after background refresh', async ({page}) => {
  await page.request.post('/api/demo/session', {data: {persona_id: 'alex'}, headers: {origin: 'http://127.0.0.1:5189'}});
  let me = await (await page.request.get('/api/me')).json();
  const created = await (await page.request.post('/api/tool-requests', {
    data: {title: 'Calendar', idempotency_key: 'concurrent-admin-browser'},
    headers: {origin: 'http://127.0.0.1:5189', 'X-CSRF-Token': me.csrf},
  })).json();
  await page.request.post('/api/demo/session', {data: {persona_id: 'admin'}, headers: {origin: 'http://127.0.0.1:5189'}});
  await page.goto('/');
  await page.getByRole('link', {name: 'Policies & approvals', exact: true}).click();
  const row = page.getByRole('row').filter({has: page.getByRole('cell', {name: 'Calendar', exact: true})});
  await row.getByRole('textbox', {name: 'Response for Calendar', exact: true}).fill('Older administrator draft.');
  me = await (await page.request.get('/api/me')).json();
  const refreshed = page.waitForResponse(r => r.request().method() === 'GET' && r.url().endsWith('/api/tool-requests'));
  await page.request.post(`/api/admin/tool-requests/${created.id}/response`, {
    data: {version: 1, status: 'FULFILLED', response: 'Newer completed administrator decision.'},
    headers: {origin: 'http://127.0.0.1:5189', 'X-CSRF-Token': me.csrf},
  });
  await refreshed;
  await expect(row.getByRole('textbox', {name: 'Response for Calendar', exact: true})).toHaveValue('Older administrator draft.');
  await row.getByRole('button', {name: 'Save response', exact: true}).click();
  await expect(page.getByText('This request changed; refresh before responding', {exact: true})).toBeVisible();
  const requests = await (await page.request.get('/api/tool-requests')).json();
  expect(requests.find((request: {id: string}) => request.id === created.id).response).toBe('Newer completed administrator decision.');
});
