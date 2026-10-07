/**
 * Rollout smoke for the scoped combined release (global-only model policy +
 * audited admin self-approval), run against a live deployment via governed
 * endpoints only. No catalog registration is persisted (negative probes fail
 * closed before any write); the single synthetic self-approved grant is
 * reverted through the governed revoke endpoint in the same test.
 *
 * Portable inputs (no secrets or environment-specific values in this file):
 *   ACCEPT_ORIGIN   application origin, e.g. the CloudFront URL
 *   E2E_CRED        chmod-600 KEY=VALUE file: ADMIN_USER/ADMIN_PW and
 *                   RESEARCH_USER/RESEARCH_PW (synthetic QA identities)
 *   E2E_CRED_SMOKE  optional chmod-600 file with SMOKE_USER/SMOKE_PW, a
 *                   temporary Cognito role-switcher (studio-admin + one
 *                   business group + studio-role-switcher) created and later
 *                   deleted by scripts/e2e_switcher_identity.py; required for
 *                   the self-approval test (plain admins cannot file requests)
 *   SMOKE_EVIDENCE  evidence output directory (default ./accept-evidence)
 */
import {test, expect, Page} from '@playwright/test';
import fs from 'fs';

const EVIDENCE = process.env.SMOKE_EVIDENCE || './accept-evidence';
fs.mkdirSync(EVIDENCE, {recursive: true});

function loadCreds(): Record<string, string> {
  const credPath = process.env.E2E_CRED;
  if (!credPath) throw new Error('Set E2E_CRED to a chmod-600 KEY=VALUE credential file');
  const creds = Object.fromEntries(fs.readFileSync(credPath, 'utf8')
    .trim().split('\n').map(line => line.split('=')));
  const smokePath = process.env.E2E_CRED_SMOKE;
  if (smokePath && fs.existsSync(smokePath)) {
    Object.assign(creds, Object.fromEntries(fs.readFileSync(smokePath, 'utf8')
      .trim().split('\n').map(line => line.split('='))));
  }
  return creds as Record<string, string>;
}

test.describe.configure({mode: 'serial'});

/** Credential entry with redacted failures: Playwright includes fill() values
 * in action logs on failure, so never let a raw error escape this helper. */
async function fillSecret(locator: ReturnType<Page['locator']>, value: string) {
  try {
    await locator.fill(value);
  } catch {
    throw new Error('credential entry failed (value redacted)');
  }
}

async function signIn(page: Page, user: string, pw: string) {
  await page.goto('/');
  await page.getByRole('button', {name: 'Sign in / Open Studio'}).click();
  await page.waitForURL(/amazoncognito\.com/, {timeout: 30000});
  const username = page.locator('input[name="username"]:visible').first();
  await username.waitFor({timeout: 20000});
  await username.fill(user);
  const pwField = page.locator('input[name="password"]:visible').first();
  if (!await pwField.isVisible().catch(() => false)) {
    await page.locator('button[type="submit"]:visible, input[type="submit"]:visible').first().click();
    await page.locator('input[name="password"]:visible').first().waitFor({timeout: 20000});
  }
  await pwField.waitFor({timeout: 20000}).catch(() => {});
  await fillSecret(page.locator('input[name="password"]:visible').first(), pw);
  await page.locator('input[name="signInSubmitButton"]:visible, button[type="submit"]:visible').first().click();
  await page.waitForURL(url => !/amazoncognito\.com/.test(url.toString()), {timeout: 60000});
}

/** Same-origin fetch helper executed in the signed-in page. */
async function api(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({method, path, body}) => {
    const me = await (await fetch('/api/me', {credentials: 'same-origin'})).json();
    const r = await fetch(path, {
      method, credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    const text = await r.text();
    let json: unknown = null;
    try { json = JSON.parse(text); } catch { /* keep text */ }
    return {status: r.status, json, text: text.slice(0, 400)};
  }, {method, path, body});
}

const get = (page: Page, path: string) => api(page, 'GET', path);

let requestId = '';

test('global-only discovery; non-global/forged registration fails closed', async ({page}) => {
  const creds = loadCreds();
  await signIn(page, creds.ADMIN_USER, creds.ADMIN_PW);

  const disc = await get(page, '/api/admin/platform/models/discovery');
  expect(disc.status, disc.text).toBe(200);
  const items = disc.json as Array<{id: string}>;
  expect(items.length, 'discovery non-empty').toBeGreaterThan(0);
  const nonGlobal = items.filter(m => !m.id.startsWith('global.'));
  expect(nonGlobal, 'every discovered id is a global.* profile').toEqual([]);

  const regionalProbe = {
    model_id: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    reason: 'Rollout smoke negative probe: regional id must be rejected'};
  const forgedProbe = {
    model_id: 'global.not-a-real-profile-v9:9',
    reason: 'Rollout smoke negative probe: forged global id must be rejected'};
  // Use a real configured workspace so the 422 can only come from the
  // discovery-membership gate, not from workspace validation.
  const adminCatalog = await get(page, '/api/admin/platform/catalog');
  expect(adminCatalog.status, adminCatalog.text).toBe(200);
  const workspaces = (adminCatalog.json as any).workspaces as string[];
  expect(workspaces.length, 'deployment has at least one workspace').toBeGreaterThan(0);

  const regional = await api(page, 'POST', '/api/admin/platform/models',
    {...regionalProbe, workspaces: [workspaces[0]]});
  expect(regional.status, regional.text).toBe(422);

  const forged = await api(page, 'POST', '/api/admin/platform/models',
    {...forgedProbe, workspaces: [workspaces[0]]});
  expect(forged.status, forged.text).toBe(422);

  fs.writeFileSync(EVIDENCE + '/rollout-smoke-discovery.json', JSON.stringify({
    discovered_ids: items.map(m => m.id), regional_probe: regional, forged_probe: forged}, null, 2));
  await page.screenshot({path: EVIDENCE + '/rollout-smoke-discovery.png', fullPage: true});
});

test('active-admin self-approval audited; business-active decision 403; grant reverted', async ({page}) => {
  const creds = loadCreds();
  expect(creds.SMOKE_USER,
    'self-approval needs the temporary role-switcher from scripts/e2e_switcher_identity.py (E2E_CRED_SMOKE)').toBeTruthy();
  await signIn(page, creds.SMOKE_USER, creds.SMOKE_PW);
  const me0 = await get(page, '/api/me');
  expect(me0.status).toBe(200);
  const persona = (me0.json as any).persona;
  const roles = (me0.json as any).roles as Array<{id: string, label: string, selected: boolean}>;
  const admin = roles.find(r => r.label === 'Platform Admin');
  const biz = roles.find(r => r.label !== 'Platform Admin');
  expect(admin, 'switcher has the admin group').toBeTruthy();
  expect(biz, 'switcher has one business group').toBeTruthy();

  // 1) Business-active: create own request on a published global model.
  const sw1 = await api(page, 'POST', '/api/auth/role', {group_id: biz!.id});
  expect(sw1.status, sw1.text).toBe(200);
  const cat = await get(page, '/api/catalog');
  expect(cat.status).toBe(200);
  const models = ((cat.json as any).items as any[]).filter(i => i.kind === 'model' && i.requestable === true);
  expect(models.length, 'at least one requestable model visible to business').toBeGreaterThan(0);
  let componentId = '';
  for (const m of models) {
    const req = await api(page, 'POST', '/api/requests', {component_id: m.id,
      reason: 'Rollout smoke: synthetic self-approval acceptance request'});
    if (req.status === 201) { componentId = m.id; requestId = (req.json as any).id; break; }
  }
  expect(requestId, 'created a synthetic pending request').toBeTruthy();

  // 2) Still business-active: deciding is denied (admin gate), nothing changes.
  const denied = await api(page, 'POST', `/api/admin/requests/${requestId}/decision`,
    {approve: true, reason: 'Rollout smoke: must be denied in business role'});
  expect(denied.status, denied.text).toBe(403);

  // 3) Switch to active Platform Admin and self-approve the same request.
  const sw2 = await api(page, 'POST', '/api/auth/role', {group_id: admin!.id});
  expect(sw2.status, sw2.text).toBe(200);
  const approved = await api(page, 'POST', `/api/admin/requests/${requestId}/decision`,
    {approve: true, reason: 'Rollout smoke: audited self-approval by active Platform Admin'});
  expect(approved.status, approved.text).toBe(200);

  // From here a synthetic grant exists: always revert it, even on assertion
  // failure, after making sure the admin role is still active.
  let detail: any = null;
  let revoke: any = null;
  try {
    const after = await get(page, '/api/requests');
    const row = (after.json as any[]).find(x => x.id === requestId);
    expect(row.status).toBe('APPROVED');

    // Audit row carries the explicit self-approval marker.
    const audit = await get(page, '/api/admin/audit');
    expect(audit.status).toBe(200);
    const entry = (audit.json as any[]).find(a => a.action === 'request_decided' && a.resource === requestId);
    expect(entry, 'request_decided audit row present').toBeTruthy();
    detail = JSON.parse(entry.detail);
    expect(detail.self_approved).toBe(true);
    expect(detail.actor_role).toBe('admin');
    expect(detail.actor).toBe(persona.id);
    expect(detail.requester).toBe(persona.id);
    expect(detail.decision).toBe('approved');
  } finally {
    await api(page, 'POST', '/api/auth/role', {group_id: admin!.id});
    revoke = await api(page, 'POST', '/api/admin/grants', {persona_id: persona.id,
      component_id: componentId, enabled: false,
      reason: 'Rollout smoke cleanup: revert synthetic self-approved grant'});
  }
  expect(revoke.status, revoke.text).toBe(200);

  fs.writeFileSync(EVIDENCE + '/rollout-smoke-selfapprove.json', JSON.stringify({
    persona: persona.id, component: componentId, request: requestId,
    business_denied: denied, approved, audit_detail: detail, revoke}, null, 2));
  await page.screenshot({path: EVIDENCE + '/rollout-smoke-selfapprove.png', fullPage: true});
});

test('plain business user cannot reach the decision endpoint (403)', async ({page}) => {
  const creds = loadCreds();
  await signIn(page, creds.RESEARCH_USER, creds.RESEARCH_PW);
  const denied = await api(page, 'POST', `/api/admin/requests/${requestId || 'synthetic-request-id'}/decision`,
    {approve: true, reason: 'Rollout smoke: research user must be denied'});
  expect(denied.status, denied.text).toBe(403);
  fs.writeFileSync(EVIDENCE + '/rollout-smoke-business403.json', JSON.stringify(denied, null, 2));
});
