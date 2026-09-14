import {test, expect, type Page} from '@playwright/test';

// UI contracts: actual signed-session authorization is verified separately.
async function registry(page: Page, probeStatus: number) {
 let denied = false;
 let changes = 0;
 let logins = 0;
 const me = {persona: {id: 'demo-member', name: 'Demo member', role: 'admin',
  workspace: 'platform', workspace_name: 'Platform governance'}, csrf: 'test-csrf'};
 await page.route('**/studio-config.json', r => r.fulfill({json: {hosted: true}}));
 await page.route('**/api/me', r => r.fulfill(denied && probeStatus !== 200
  ? {status: probeStatus, json: {message: 'Session unavailable'}} : {json: me}));
 await page.route('**/api/admin/catalog', r => r.fulfill({json: {
  foundations: [], components: [{id: 'evidence-citations', name: 'Evidence citations',
   description: 'Cite sources for factual claims.', kind: 'skill', provider: 'Platform skill library',
   version: '1', approved: true, fixture: false}],
  personas: [{...me.persona, role: 'business', workspace: 'research'}],
  grants: [{persona: 'demo-member', component: 'evidence-citations'}], policy: {}, history: [],
 }}));
 await page.route('**/api/admin/grants', r => {
  changes++;
  expect(r.request().postDataJSON()).toEqual({
   persona_id: 'demo-member', component_id: 'evidence-citations', enabled: false, reason: 'revoke',
  });
  denied = true;
  return r.fulfill({status: 403, json: {message: 'Forbidden'}});
 });
 await page.route('**/auth/login', r => {
  logins++;
  return r.fulfill({contentType: 'text/html', body: '<h1>Server sign-in flow</h1>'});
 });
 await page.goto('/');
 await page.getByRole('tab', {name: 'Tool / skill registry', exact: true}).click();
 await page.getByRole('textbox', {name: 'Grant reason for evidence-citations'}).fill('revoke');
 await page.getByRole('checkbox', {name: 'Demo member access to Evidence citations'}).click();
 return {changes: () => changes, logins: () => logins};
}

for (const status of [401, 403]) {
 test(`expired admin session (${status}) offers explicit sign-in without retrying revocation`, async ({page}) => {
  const calls = await registry(page, status);
  await expect(page.getByText('Sign in required', {exact: true})).toBeVisible();
  await expect(page.getByText('Your session is no longer available. Sign in again to continue.', {exact: true})).toBeVisible();
  await expect(page.getByText('Access is restricted. Request access or contact your administrator.', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('checkbox', {name: 'Demo member access to Evidence citations'})).toBeChecked();
  await expect(page.getByRole('textbox', {name: 'Grant reason for evidence-citations'})).toHaveValue('revoke');
  expect(calls.changes()).toBe(1);
  expect(calls.logins()).toBe(0);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Server sign-in flow'})).toBeVisible();
  expect(calls.logins()).toBe(1);
  expect(calls.changes()).toBe(1);
 });
}

for (const status of [200, 503]) {
 test(`grant denial with session probe ${status} is not reported as an expired session`, async ({page}) => {
  const calls = await registry(page, status);
  await expect(page.getByText('Access is restricted. Request access or contact your administrator.', {exact: true})).toBeVisible();
  await expect(page.getByText('Sign in required', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Sign in', exact: true})).toHaveCount(0);
  expect(calls.changes()).toBe(1);
  expect(calls.logins()).toBe(0);
 });
}
