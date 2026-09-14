import {test, expect} from '@playwright/test';

// Browser contract; signed-token and DynamoDB tests cover actual revocation.
for (const denied of [false, true]) {
 test(`Sign out reaches server logout with ${denied ? 'denied' : 'loaded'} session`, async ({page}) => {
  await page.route('**/studio-config.json', r => r.fulfill({json: {hosted: true}}));
  await page.route('**/api/me', r => r.fulfill(denied
   ? {status: 403, json: {message: 'Forbidden'}}
   : {json: {persona: {id: 'member', name: 'Demo member', role: 'business',
      workspace: 'research', workspace_name: 'Research studio'}, csrf: 'old-csrf'}}));
  await page.route('**/api/agents', r => r.fulfill({json: []}));
  await page.route('**/auth/verification/status', r => r.fulfill({status: 401, json: {detail: 'No pending verification'}}));
  let calls = 0;
  await page.route('**/api/auth/logout', async r => {
   expect(r.request().method()).toBe('POST');
   calls++;
   await r.fulfill({json: {logout_url: '/signed-out'}});
  });
  await page.route('**/signed-out', r => r.fulfill({contentType: 'text/html', body: '<h1>Signed out</h1>'}));
  await page.goto('/');
  const menu = denied ? 'Session unavailable' : 'Demo member · Research studio';
  await page.getByRole('button', {name: menu, exact: true}).click();
  await page.getByRole('menuitem', {name: 'Sign out', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Signed out', exact: true})).toBeVisible();
  expect(calls).toBe(1);
 });
}
