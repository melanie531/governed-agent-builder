import {test, expect, type Page} from '@playwright/test';

async function setup(page: Page, scenario = 'normal') {
  const calls: {path: string; body: any}[] = [], errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  let phase = scenario === 'native-retry' ? 'NEEDS_RECONCILIATION' : scenario === 'management' ? 'READY' : 'REVIEW';
  let name = 'Company data', revision = 1;
  let auth = {id: 'company', name: 'Company service PAT', revision: 1, allowed_origins: ['https://data.example.com'],
    auth_type: 'API_KEY', header: 'Authorization', prefix: 'Bearer', references: [], can_edit: true, can_delete: true, reason: ''};
  let authDeleted = false;
  const state = () => ({id: 'remote-1', job_id: 'job-1', name, description: '', revision,
    endpoint: 'https://data.example.com/mcp', connection_id: 'company', workspaces: ['research'],
    phase, retry_available: scenario === 'native-retry', discovery_digest: 'a'.repeat(64), gateway_target_id: 'target-1',
    registry_record_arn: 'arn:aws:agent-registry:us-east-1:123456789012:registry/r/record/t',
    tools: [{name: 'list_datasets', description: 'Discover datasets.', inputSchema: {type: 'object', properties: {}}}]});
  await page.route('**/studio-config.json', route => route.fulfill({json: {hosted: true, journey_enabled: true}}));
  await page.route('**/api/**', route => {
    const r = route.request(), path = new URL(r.url()).pathname;
    if (r.method() === 'POST') calls.push({path, body: r.postDataJSON()});
    if (path === '/api/me') return route.fulfill({json: {persona: {id: 'admin', role: 'admin', workspace: 'platform'}, csrf: 'test-csrf'}});
    if (path === '/api/admin/platform/overview') return route.fulfill({json: {agents: [], hours: 24, pending_tool_requests: 0, pending_access_requests: 0}});
    if (path === '/api/admin/catalog') return route.fulfill({json: {foundations: [], components: [], grants: [], personas: [], history: [], policy: {}}});
    if (path === '/api/agents') return route.fulfill({json: []});
    if (path === '/api/admin/mcp/onboarding-options') return route.fulfill({json: {
      enabled: true, credential_setup: true, workspaces: ['research'], connections: [{id: 'company', name: 'Company service PAT',
        auth_type: 'API_KEY', allowed_origins: ['https://data.example.com']}]}});
    if (path === '/api/admin/mcp/servers') return route.fulfill({json: {items: []}});
    if (path === '/api/admin/mcp/auth-connections') return route.fulfill({json: {items: authDeleted ? [] : [auth]}});
    if (path === '/api/admin/mcp/auth-connections/company/edit') {
      auth = {...auth, name: r.postDataJSON().name, revision: 2};
      return route.fulfill({json: {phase: 'READY'}});
    }
    if (path === '/api/admin/mcp/auth-connections/company/delete') {
      authDeleted = true; return route.fulfill({json: {phase: 'DELETED'}});
    }
    if (path === '/api/admin/mcp/management/onboarding/remote-1') return route.fulfill({json: {
      ...state(), can_edit: true, can_delete: true, blockers: [], reason: ''}});
    if (path === '/api/admin/mcp/management/onboarding/remote-1/edit') {
      name = r.postDataJSON().name; revision = 2; phase = 'REVIEW';
      return route.fulfill({status: 202, json: {id: 'remote-1'}});
    }
    if (path === '/api/admin/mcp/management/onboarding/remote-1/delete') {
      phase = 'DELETED'; return route.fulfill({status: 202, json: {id: 'remote-1'}});
    }
    if (path === '/api/admin/mcp/credentials') {
      if (scenario === 'auth-uncertain' && calls.length === 1) return route.abort('failed');
      if (scenario === 'auth-retry') return route.fulfill({json: {
        phase: 'NEEDS_RECONCILIATION', retry_count: 0, retry_available: true, failure_code: 'AccessDeniedException'}});
      return route.fulfill({json: {phase: 'READY', connection_id: 'company'}});
    }
    if (path.startsWith('/api/admin/mcp/credentials/') && path.endsWith('/retry')) return route.fulfill({json: {phase: 'READY', connection_id: 'company'}});
    if (path.startsWith('/api/admin/mcp/credentials/')) return route.fulfill({status: 404, json: {detail: 'Authentication request not found'}});
    if (path === '/api/admin/mcp/onboarding' && r.method() === 'GET') return route.fulfill({json: {items: scenario === 'management' && phase !== 'DELETED' ? [state()] : []}});
    if (path === '/api/admin/mcp/onboarding' && r.method() === 'POST') {
      if (calls.length === 1 && scenario === 'rejected') return route.fulfill({status: 422, json: {detail: 'Endpoint configuration changed'}});
      if (calls.length === 1 && scenario === 'uncertain') return route.abort('failed');
      return route.fulfill({status: 202, json: {id: 'remote-1', phase: 'CONNECTING'}});
    }
    if (path.startsWith('/api/admin/mcp/onboarding-requests/')) return route.fulfill({status: 404, json: {detail: 'Onboarding request not found'}});
    if (path === '/api/admin/mcp/onboarding/remote-1/publish') {
      phase = 'READY'; return route.fulfill({status: 202, json: {id: 'remote-1', phase: 'SUBMITTING'}});
    }
    if (path === '/api/admin/mcp/onboarding/remote-1/retry') {
      phase = 'REVIEW'; return route.fulfill({status: 202, json: {id: 'remote-1', phase: 'REGISTERING'}});
    }
    if (path === '/api/admin/mcp/onboarding/remote-1') return route.fulfill({json: state()});
    return route.fulfill({status: 404, json: {detail: 'Unexpected route'}});
  });
  await page.goto('/');
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  if (scenario === 'management') return {calls, errors};
  await page.getByRole('button', {name: 'Create MCP connection', exact: true}).click();
  await page.getByLabel('Connection name', {exact: true}).fill('Company data');
  await page.getByLabel('MCP endpoint URL', {exact: true}).fill('https://data.example.com/mcp');
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'Existing connection', exact: true}).click();
  return {calls, errors};
}

test('one generic creation flow has no vendor tabs or profile requirements', async ({page}) => {
  const {calls, errors} = await setup(page);
  await expect(page.getByRole('tab')).toHaveCount(0);
  await expect(page.getByText('Snowflake profile', {exact: true})).toHaveCount(0);
  await expect(page.getByText('Create Snowflake server', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'API key / PAT', exact: true}).click();
  await page.getByLabel('API key or PAT', {exact: true}).fill('test-value-never-persist');
  await page.getByRole('button', {name: 'Save authentication', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  expect(calls[0].body).toMatchObject({endpoint: 'https://data.example.com/mcp',
    header: 'Authorization', prefix: 'Bearer', secret: 'test-value-never-persist'});
  const storage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}}));
  expect(storage).not.toContain('test-value-never-persist');
  await expect(page.getByLabel('API key or PAT', {exact: true})).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('standalone saved credentials can be edited and deleted without retaining the secret', async ({page}) => {
  const {calls, errors} = await setup(page, 'management');
  await page.getByRole('row').filter({hasText: 'Company service PAT'}).getByRole('radio').check();
  await page.getByRole('button', {name: 'Edit authentication', exact: true}).click();
  await page.getByLabel('Authentication name', {exact: true}).fill('Updated service PAT');
  await page.getByLabel('Replacement API key or PAT', {exact: true}).fill('replacement-secret-not-persisted');
  await page.getByRole('button', {name: 'Save authentication changes', exact: true}).click();
  await expect(page.getByText('Authentication connection updated.', {exact: true})).toBeVisible();
  expect(calls[0].body).toMatchObject({name: 'Updated service PAT', expected_revision: 1, secret: 'replacement-secret-not-persisted'});
  expect(await page.evaluate(() => JSON.stringify({...sessionStorage}))).not.toContain('replacement-secret-not-persisted');
  await page.getByRole('button', {name: 'Delete authentication', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Confirm authentication deletion', exact: true})).toBeDisabled();
  await page.getByLabel('Confirm authentication name').fill('Updated service PAT');
  await page.getByRole('button', {name: 'Confirm authentication deletion', exact: true}).click();
  await expect(page.getByText('Authentication connection deleted.', {exact: true})).toBeVisible();
  expect(calls[1].body).toMatchObject({expected_revision: 2, confirm_name: 'Updated service PAT'});
  expect(errors).toEqual([]);
});

test('existing MCP registration exposes edit and delete with a new tool review', async ({page}) => {
  const {calls, errors} = await setup(page, 'management');
  await page.getByRole('row').filter({hasText: 'Company data'}).getByRole('radio').check();
  await page.getByRole('button', {name: 'Edit connection', exact: true}).click();
  await page.getByLabel('Edit connection name', {exact: true}).fill('Updated MCP');
  await page.getByRole('button', {name: 'Save and rediscover', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
  expect(calls[0].body).toMatchObject({name: 'Updated MCP', expected_revision: 1});
  expect(calls.some(c => c.path.endsWith('/publish'))).toBe(false);
  await page.getByRole('button', {name: 'Delete connection', exact: true}).click();
  await page.getByLabel('Confirm connection name').fill('Updated MCP');
  await page.getByRole('button', {name: 'Confirm connection deletion', exact: true}).click();
  await expect(page.getByText('Connection deleted. Its remote MCP server and saved authentication were preserved.')).toBeVisible();
  expect(calls[1].body).toMatchObject({confirm_name: 'Updated MCP', expected_revision: 2});
  expect(errors).toEqual([]);
});

test('an unrecorded credential request can be retried explicitly with the same key and a reentered secret', async ({page}) => {
  const {calls} = await setup(page, 'auth-uncertain');
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'API key / PAT', exact: true}).click();
  await page.getByLabel('API key or PAT', {exact: true}).fill('test-secret');
  await page.getByRole('button', {name: 'Save authentication', exact: true}).click();
  await page.getByRole('button', {name: 'Check authentication status', exact: true}).click();
  await page.getByLabel('API key or PAT', {exact: true}).fill('test-secret');
  await page.getByRole('button', {name: 'Retry authentication request', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  expect(calls[1].body.idempotency_key).toBe(calls[0].body.idempotency_key);
});

test('provider retry is explicit, bounded to the displayed attempt, and sends no secret', async ({page}) => {
  const {calls} = await setup(page, 'auth-retry');
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'API key / PAT', exact: true}).click();
  await page.getByLabel('API key or PAT', {exact: true}).fill('test-secret');
  await page.getByRole('button', {name: 'Save authentication', exact: true}).click();
  await page.getByRole('button', {name: 'Retry provider creation', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  expect(calls[1].body).toEqual({expected_attempt: 0});
});

test('onboards a generic endpoint, reviews discovered tools, and explicitly publishes', async ({page}) => {
  const {calls, errors} = await setup(page);
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
  expect(calls.filter(c => c.path.endsWith('/publish'))).toHaveLength(0);
  await page.getByRole('checkbox', {name: 'list_datasets', exact: true}).check();
  await page.getByRole('button', {name: 'Approve and publish', exact: true}).click();
  await expect(page.getByText('Connection published', {exact: true})).toBeVisible();
  expect(calls[0].body).toMatchObject({endpoint: 'https://data.example.com/mcp', connection_id: 'company', workspaces: ['research']});
  expect(calls[1].body).toMatchObject({discovery_digest: 'a'.repeat(64), tools: ['list_datasets']});
  expect(errors).toEqual([]);
});

test('definitive validation rejection leaves the form editable', async ({page}) => {
  const {calls} = await setup(page, 'rejected');
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Endpoint configuration changed', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Connect and discover', exact: true})).toBeEnabled();
  expect(calls).toHaveLength(1);
  await page.getByLabel('Connection name', {exact: true}).fill('Updated connection');
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
});

test('uncertain create requires GET before an explicit same-key retry', async ({page}) => {
  const {calls} = await setup(page, 'uncertain');
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Saved onboarding request', {exact: true})).toBeVisible();
  expect(calls).toHaveLength(1);
  await page.getByRole('button', {name: 'Check status', exact: true}).click();
  await page.getByRole('button', {name: 'Retry retained request', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
  expect(calls).toHaveLength(2);
  expect(calls[1].body).toEqual(calls[0].body);
});

test('native creation retry requires an explicit action bound to the displayed job', async ({page}) => {
  const {calls} = await setup(page, 'native-retry');
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Retry original request', exact: true})).toBeVisible();
  expect(calls).toHaveLength(1);
  await page.getByRole('button', {name: 'Retry original request', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
  expect(calls[1]).toEqual({path: '/api/admin/mcp/onboarding/remote-1/retry', body: {job_id: 'job-1'}});
});
