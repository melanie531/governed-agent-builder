import {test, expect} from '@playwright/test';

// UI contract only; real Cognito checks are recorded separately.
test('enrolled user switches roles through the hosted account menu', async ({page}) => {
 let admin = false;
 const switches: string[] = [];
 await page.route('**/studio-config.json', r => r.fulfill({json: {hosted: true}}));
 await page.route('**/api/me', r => r.fulfill({json: {
  persona: {id: 'member', name: 'Demo member', role: admin ? 'admin' : 'business',
   workspace: admin ? 'platform' : 'research', workspace_name: admin ? 'Platform governance' : 'Research studio'},
  csrf: 'test-csrf',
  roles: [{id: 'studio-research', label: 'Business User', selected: !admin},
   {id: 'studio-admin', label: 'Platform Admin', selected: admin}],
 }}));
 await page.route('**/api/agents', r => r.fulfill({json: []}));
 await page.route('**/api/admin/catalog', r => r.fulfill({json: {
  foundations: [], components: [], grants: [], personas: [], history: [], policy: {},
 }}));
 await page.route('**/api/auth/role', async r => {
  expect(r.request().headers()['x-csrf-token']).toBe('test-csrf');
  const group = r.request().postDataJSON().group_id;
  switches.push(group);
  admin = group === 'studio-admin';
  await r.fulfill({json: {role: admin ? 'admin' : 'business'}});
 });
 await page.goto('/');
 await expect(page.getByRole('heading', {name: 'My agents', exact: true})).toBeVisible();
 await page.getByRole('button', {name: 'Demo member · Research studio'}).click();
 await page.getByRole('menuitem', {name: 'Platform Admin', exact: true}).click();
 await expect(page.getByRole('button', {name: 'Demo member · Platform governance'})).toBeVisible();
 await expect(page.getByRole('link', {name: 'Policies & approvals', exact: true})).toBeVisible();
 await page.reload();
 await page.getByRole('button', {name: 'Demo member · Platform governance'}).click();
 await page.getByRole('menuitem', {name: 'Business User', exact: true}).click();
 await expect(page.getByRole('heading', {name: 'My agents', exact: true})).toBeVisible();
 await expect(page.getByRole('link', {name: 'Policies & approvals', exact: true})).toHaveCount(0);
 expect(switches).toEqual(['studio-admin', 'studio-research']);
});
