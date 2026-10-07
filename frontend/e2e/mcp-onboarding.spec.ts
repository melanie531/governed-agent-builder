import {test, expect, type Page} from '@playwright/test';
import {createHash} from 'node:crypto';

async function setup(page: Page, scenario = 'normal') {
  const calls: {path: string; body: any}[] = [], errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  let phase = scenario === 'native-retry' ? 'NEEDS_RECONCILIATION' : scenario === 'management' ? 'READY' : 'REVIEW';
  let name = 'Company data', revision = 1;
  let auth = {id: 'company', name: 'Company service PAT', revision: 1, allowed_origins: ['https://data.example.com'],
    auth_type: 'API_KEY', header: 'Authorization', prefix: 'Bearer', references: [], can_edit: true, can_delete: true, reason: ''};
  let authDeleted = false;
  let iamEndpoint = '';
  let oauthSaved = false;
  let oauthGrant = 'AUTHORIZATION_CODE';
  let suppliedTools: any[] | undefined;
  let python: any;
  let archiveUpload: any, partReceived = false, packageInterrupted = false;
  const state = () => ({id: 'remote-1', job_id: 'job-1', name, description: '', revision,
    endpoint: 'https://data.example.com/mcp', connection_id: 'company', workspaces: ['research'],
    phase, ...(scenario === 'oauth' ? {schema_source: 'supplied'} : {}),
    retry_available: scenario === 'native-retry', discovery_digest: 'a'.repeat(64), gateway_target_id: 'target-1',
    registry_record_arn: 'arn:aws:agent-registry:us-east-1:123456789012:registry/r/record/t',
    tools: suppliedTools || python?.tools || [{name: 'list_datasets', description: 'Discover datasets.', inputSchema: {type: 'object', properties: {}}}]});
  await page.route('**/studio-config.json', route => route.fulfill({json: {hosted: true, journey_enabled: true}}));
  await page.route('**/api/**', route => {
    const r = route.request(), path = new URL(r.url()).pathname;
    if (r.method() === 'POST') calls.push({path, body: r.postDataJSON()});
    if (path === '/api/me') return route.fulfill({json: {persona: {id: 'admin', name: 'QA administrator',
      role: 'admin', workspace: 'platform', workspace_name: 'Platform governance'}, csrf: 'test-csrf'}});
    if (path === '/api/admin/platform/overview') return route.fulfill({json: {agents: [], hours: 24, pending_tool_requests: 0, pending_access_requests: 0}});
    if (path === '/api/admin/catalog') return route.fulfill({json: {foundations: [], components: [], grants: [], personas: [], history: [], policy: {}}});
    if (path === '/api/agents') return route.fulfill({json: []});
    if (path === '/api/admin/mcp/onboarding-options') return route.fulfill({json: {
      enabled: true, credential_setup: true, runtime_iam_setup: true, oauth_setup: true,
      python_bundle: 'Python 3.13, FastMCP and Snowflake', python_packages: true,
      workspaces: ['research'], connections: [{id: 'company', name: 'Company service PAT',
        auth_type: 'API_KEY', allowed_origins: ['https://data.example.com']},
        ...(oauthSaved ? [{id: 'user-oauth', name: 'User data OAuth', auth_type: 'OAUTH', requires_schema: oauthGrant === 'AUTHORIZATION_CODE',
          callback_url: 'https://identity.example.com/agentcore/callback',
          allowed_origins: [python ? 'https://python.example.com' : 'https://data.example.com']}] : []),
        ...(iamEndpoint ? [{id: 'runtime-iam', name: 'Company data IAM', auth_type: 'GATEWAY_IAM_ROLE',
          allowed_origins: [new URL(iamEndpoint).origin]}] : [])]}});
    if (path === '/api/admin/mcp/packages' && r.method() === 'POST') {
      archiveUpload = {connection_mode: 'PACKAGE', ...r.postDataJSON(), id: 'c'.repeat(32), phase: 'UPLOADING', part_bytes: 2 * 1024 * 1024,
        part_count: 1, received_parts: []};
      return route.fulfill({status: 201, json: archiveUpload});
    }
    if (path.startsWith('/api/admin/mcp/packages/') && path.includes('/parts/')) {
      if (scenario === 'package-uncertain' && !packageInterrupted) {
        packageInterrupted = true; return route.abort('failed');
      }
      partReceived = true; archiveUpload.received_parts = [0];
      return route.fulfill({json: archiveUpload});
    }
    if (path.startsWith('/api/admin/mcp/packages/') && path.endsWith('/deploy')) {
      if (!partReceived) return route.fulfill({status: 409, json: {detail: 'Incomplete archive'}});
      python = {...archiveUpload, upload_type: 'package', job_id: 'd'.repeat(32), phase: 'READY', stage: 'schema',
        endpoint: archiveUpload.connection_mode === 'SNOWFLAKE_OAUTH' ? 'https://python.example.com/mcp/' + archiveUpload.id
          : 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fstudio_python_test-1234567890/invocations?qualifier=DEFAULT',
        tools: [{name: 'package_greeting', description: 'Return a greeting from a support module.', inputSchema: {type: 'object', properties: {}}}]};
      if (scenario === 'package-bearer') python.bearer_endpoint = 'https://python.example.com/mcp/' + archiveUpload.id;
      if (scenario.startsWith('package-discovery-')) {
        python = {...python, phase: 'NEEDS_RECONCILIATION', tools: [],
          failure_code: scenario.endsWith('legacy') ? 'ValueError' : 'MCP_DISCOVERY_FAILED'};
      }
      return route.fulfill({status: 202, json: python});
    }
    if (path.startsWith('/api/admin/mcp/packages/') || path.startsWith('/api/admin/mcp/package-requests/')) {
      return route.fulfill({json: python || archiveUpload});
    }
    if (path === '/api/admin/mcp/python' && r.method() === 'GET') return route.fulfill({json: {items: python ? [python] : []}});
    if (path === '/api/admin/mcp/deployments') return route.fulfill({json: {items: []}});
    if (path === '/api/admin/mcp/python' && r.method() === 'POST') {
      const payload = r.postDataJSON();
      python = {id: 'a'.repeat(32), job_id: 'b'.repeat(32), phase: 'PACKAGING', name: payload.name, filename: payload.filename,
        endpoint: 'https://python.example.com/mcp/' + 'a'.repeat(32), source_digest: createHash('sha256').update(payload.source).digest('hex'),
        tools: [{name: 'list_datasets', description: 'Discover datasets.', inputSchema: {type: 'object', properties: {}}}]};
      if (scenario === 'python-uncertain') return route.abort('failed');
      return route.fulfill({status: 202, json: python});
    }
    if (path.startsWith('/api/admin/mcp/python-requests/')) return route.fulfill({json: {...python, phase: 'READY'}});
    if (path.startsWith('/api/admin/mcp/python/')) return route.fulfill({json: python?.upload_type === 'package' ? python : {...python, phase: 'READY'}});
    if (path === '/api/admin/mcp/oauth-credentials') {
      oauthSaved = true;
      return route.fulfill({json: {phase: 'READY', connection_id: 'user-oauth'}});
    }
    if (path === '/api/admin/mcp/iam-credentials') {
      if (scenario === 'iam-uncertain' && calls.length === 1) return route.abort('failed');
      iamEndpoint = r.postDataJSON().endpoint;
      return route.fulfill({json: {phase: 'READY', connection_id: 'runtime-iam'}});
    }
    if (path.startsWith('/api/admin/mcp/iam-credentials/')) {
      return route.fulfill({status: 404, json: {detail: 'IAM authentication request not found'}});
    }
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
      if (r.postDataJSON().auth_type === 'OAUTH') {
        if (scenario === 'managed-oauth-uncertain' && calls.length === 1) return route.abort('failed');
        oauthSaved = true; oauthGrant = r.postDataJSON().grant_type;
        return route.fulfill({json: {phase: 'READY', connection_id: 'user-oauth'}});
      }
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
      suppliedTools = r.postDataJSON().tool_schema;
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
  await page.getByRole('button', {name: 'Add MCP connection', exact: true}).click();
  if (scenario === 'entry') return {calls, errors};
  await page.getByLabel('Connection name', {exact: true}).fill('Company data');
  await page.getByLabel('MCP endpoint URL', {exact: true}).fill('https://data.example.com/mcp');
  if (scenario.startsWith('package-')) return {calls, errors};
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'Existing connection', exact: true}).click();
  await page.getByRole('button', {name: /^Authentication connection/}).click();
  await page.getByRole('option', {name: 'Company service PAT API_KEY', exact: true}).click();
  return {calls, errors};
}

test('complete ZIP upload carries its own package and offers generic IAM after deployment', async ({page}, testInfo) => {
  const {calls, errors} = await setup(page, 'entry');
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  await page.getByLabel('Connection name', {exact: true}).fill('My complete MCP');
  await expect(page.getByText('Package connection', {exact: true})).toHaveCount(0);
  await expect(page.getByLabel('Snowflake account', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: /^Authentication method/})).toHaveCount(0);
  const file = Buffer.from('PK\x03\x04my-complete-package-and-support-module');
  await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({name: 'my-mcp.zip', mimeType: 'application/zip', buffer: file});
  await expect(page.getByRole('button', {name: 'Upload and deploy package', exact: true})).toBeEnabled();
  await page.screenshot({path: testInfo.outputPath('complete-package-desktop.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Upload and deploy package', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Use this MCP package', exact: true})).toBeVisible();
  const posts = calls.filter(c => c.path.includes('/packages'));
  expect(posts.map(c => c.path)).toEqual(['/api/admin/mcp/packages',
    '/api/admin/mcp/packages/' + 'c'.repeat(32) + '/parts/0',
    '/api/admin/mcp/packages/' + 'c'.repeat(32) + '/deploy']);
  expect(posts[0].body).toMatchObject({filename: 'my-mcp.zip', size: file.length,
    source_digest: createHash('sha256').update(file).digest('hex')});
  expect(posts[0].body).not.toHaveProperty('connection_mode');
  expect(Buffer.from(posts[1].body.data, 'base64')).toEqual(file);
  expect(await page.evaluate(() => JSON.stringify({...sessionStorage}))).not.toContain(file.toString('base64'));
  await page.getByRole('button', {name: 'Use this MCP package', exact: true}).click();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'AWS IAM / AgentCore Runtime', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Save IAM connection', exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});

test('generic upload has no provider controls or single-file bundle option', async ({page}) => {
  await setup(page, 'entry');
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  await expect(page.getByRole('radio', {name: /Snowflake/})).toHaveCount(0, {timeout: 3000});
  await expect(page.getByRole('radio', {name: /Upload Python file/})).toHaveCount(0);
  await expect(page.getByLabel(/Snowflake (account|reader role|warehouse)/)).toHaveCount(0);
  await expect(page.getByText('Package connection', {exact: true})).toHaveCount(0);
});

test('saved MCP deployment exposes copyable OAuth and IAM endpoints before onboarding', async ({page, context}, testInfo) => {
  const {calls, errors} = await setup(page, 'entry');
  const oauthEndpoint = 'https://python.example.com/mcp/ffffffffffffffffffffffffffffffff';
  const iamEndpoint = 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/' +
    'arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fstudio_python_test-1234567890/invocations?qualifier=DEFAULT';
  await page.route('**/api/admin/mcp/deployments', route => route.fulfill({json: {items: [{
    id: 'f'.repeat(32), name: 'Endpoint lookup MCP', filename: 'complete.zip', phase: 'READY',
    revision: 1, can_delete: true, reason: '', blockers: [], connection_mode: 'PACKAGE',
    endpoint: iamEndpoint, bearer_endpoint: oauthEndpoint,
  }]}}));
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.reload();
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('button', {name: 'Manage uploaded MCP servers', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Server', exact: true})).toHaveCount(0);
  await page.getByRole('radio', {name: 'Select Endpoint lookup MCP', exact: true}).check();
  await expect(page.getByText(oauthEndpoint, {exact: true})).toBeVisible();
  await expect(page.getByText(iamEndpoint, {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Copy MCP endpoint URL', exact: true}).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(oauthEndpoint);
  await page.getByRole('button', {name: 'Copy AWS IAM endpoint URL', exact: true}).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(iamEndpoint);
  await page.screenshot({path: testInfo.outputPath('deployment-endpoints-desktop.png'), fullPage: true, animations: 'disabled'});
  await page.setViewportSize({width: 390, height: 844});
  await page.getByText(oauthEndpoint, {exact: true}).scrollIntoViewIfNeeded();
  await expect(page.getByText(oauthEndpoint, {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Copy MCP endpoint URL', exact: true})).toBeVisible();
  await expect(page.getByText(oauthEndpoint, {exact: true})).toBeInViewport();
  await page.screenshot({path: testInfo.outputPath('deployment-endpoints-mobile.png'), fullPage: true, animations: 'disabled'});
  expect(calls).toEqual([]);
  expect(errors).toEqual([]);
});

test('uploaded MCP deployment can be deleted with an exact-name confirmation', async ({page}, testInfo) => {
  await setup(page, 'entry');
  let deleted = false;
  const mutations: any[] = [];
  const deployment = {id: 'f'.repeat(32), name: 'Disposable MCP', filename: 'complete.zip', phase: 'READY',
    revision: 1, can_delete: true, reason: '', blockers: [], created: 1, upload_type: 'package'};
  await page.route('**/api/admin/mcp/deployments**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      mutations.push(request.postDataJSON()); deleted = true;
      return route.fulfill({status: 202, json: {...deployment, phase: 'DELETED', deleting: true}});
    }
    return route.fulfill({json: path.endsWith('/deployments') ? {items: deleted ? [] : [deployment]}
      : {...deployment, phase: deleted ? 'DELETED' : 'READY'}});
  });
  await page.reload();
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('button', {name: 'Manage uploaded MCP servers', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Uploaded MCP deployments', exact: true})).toBeVisible({timeout: 3000});
  await page.getByRole('radio', {name: 'Select Disposable MCP', exact: true}).check();
  await page.getByRole('button', {name: 'Delete MCP deployment', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Confirm deployment deletion', exact: true})).toBeDisabled();
  await page.getByLabel('Confirm deployment name', {exact: true}).fill('Disposable MCP');
  await page.screenshot({path: testInfo.outputPath('delete-deployment-confirmation.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Confirm deployment deletion', exact: true}).click();
  await expect(page.getByText('MCP deployment deleted.', {exact: true})).toBeVisible();
  expect(mutations).toHaveLength(1);
  expect(mutations[0]).toMatchObject({confirm_name: 'Disposable MCP', expected_revision: 1});
  await expect(page.getByRole('radio', {name: 'Select Disposable MCP', exact: true})).toHaveCount(0);
});

for (const persisted of [false, true]) {
  test(`uncertain deployment deletion checks its saved result before retry: persisted=${persisted}`, async ({page}) => {
    await setup(page, 'entry');
    let deleted = false;
    const mutations: any[] = [];
    const deployment = {id: 'f'.repeat(32), name: 'Retained deletion', filename: 'complete.zip',
      phase: 'READY', revision: 1, can_delete: true, reason: '', blockers: [], created: 1};
    await page.route('**/api/admin/mcp/deployments**', route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (request.method() === 'POST') {
        mutations.push(request.postDataJSON());
        if (mutations.length === 1) {deleted = persisted; return route.abort('failed');}
        deleted = true;
        return route.fulfill({status: 202, json: {...deployment, phase: 'DELETED', deleting: true}});
      }
      return route.fulfill({json: path.endsWith('/deployments') ? {items: deleted ? [] : [deployment]}
        : {...deployment, phase: deleted ? 'DELETED' : 'READY'}});
    });
    await page.reload();
    await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
    await page.getByRole('button', {name: 'Manage uploaded MCP servers', exact: true}).click();
    await page.getByRole('radio', {name: 'Select Retained deletion', exact: true}).check();
    await page.getByRole('button', {name: 'Delete MCP deployment', exact: true}).click();
    await page.getByLabel('Confirm deployment name', {exact: true}).fill('Retained deletion');
    await page.getByRole('button', {name: 'Confirm deployment deletion', exact: true}).click();
    await expect(page.getByRole('button', {name: 'Check deletion status', exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: 'Retry retained deletion request', exact: true})).toHaveCount(0);
    await page.reload();
    await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
    await page.getByRole('button', {name: 'Manage uploaded MCP servers', exact: true}).click();
    expect(mutations).toHaveLength(1);
    if (!persisted) {
      await page.getByRole('button', {name: 'Check deletion status', exact: true}).click();
      await page.getByRole('button', {name: 'Retry retained deletion request', exact: true}).click();
      expect(mutations).toHaveLength(2);
      expect(mutations[1]).toEqual(mutations[0]);
    }
    await expect(page.getByText('MCP deployment deleted.', {exact: true})).toBeVisible();
    expect(await page.evaluate(() => sessionStorage.getItem('mcp-deployment-deletion:admin:platform'))).toBeNull();
  });
}

test('deployment deletion shows saved-agent blockers and disables the destructive action', async ({page}) => {
  await setup(page, 'entry');
  await page.route('**/api/admin/mcp/deployments', route => route.fulfill({json: {items: [{
    id: 'f'.repeat(32), name: 'In-use MCP', filename: 'in-use.zip', phase: 'READY', revision: 1,
    can_delete: false, reason: 'This deployment is used by saved agents.',
    blockers: [{id: 'working-agent', name: 'Working agent', kind: 'agent'}],
  }]}}));
  await page.reload();
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('button', {name: 'Manage uploaded MCP servers', exact: true}).click();
  await page.getByRole('radio', {name: 'Select In-use MCP', exact: true}).check();
  await expect(page.getByText(/This deployment is used by saved agents. Working agent/)).toBeVisible();
  await expect(page.getByRole('button', {name: 'Delete MCP deployment', exact: true})).toBeDisabled();
});

test('an interrupted package upload waits for explicit resume of the same ZIP', async ({page}) => {
  const {calls} = await setup(page, 'package-uncertain');
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  const file = Buffer.from('PK\x03\x04complete-package');
  await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({name: 'mine.zip', mimeType: 'application/zip', buffer: file});
  await page.getByRole('button', {name: 'Upload and deploy package', exact: true}).click();
  await expect(page.getByText(/Package upload stopped/)).toBeVisible();
  expect(calls.filter(c => c.path.includes('/parts/'))).toHaveLength(1);
  expect(calls.filter(c => c.path.endsWith('/deploy'))).toHaveLength(0);
  await page.reload();
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('button', {name: 'Add MCP connection', exact: true}).click();
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  await expect(page.getByText('Retained package: mine.zip.', {exact: false})).toBeVisible();
  await page.getByRole('button', {name: 'Check package status', exact: true}).click();
  await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({name: 'mine.zip', mimeType: 'application/zip', buffer: Buffer.from('changed')});
  await expect(page.getByRole('button', {name: 'Resume same package', exact: true})).toBeDisabled();
  await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({name: 'mine.zip', mimeType: 'application/zip', buffer: file});
  await page.getByRole('button', {name: 'Resume same package', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Use this MCP package', exact: true})).toBeVisible();
  expect(calls.filter(c => c.path === '/api/admin/mcp/packages')).toHaveLength(1);
  expect(calls.filter(c => c.path.includes('/parts/'))[1].body.retry).toBe(true);
});

test('a package owns provider configuration and offers separate OAuth steps after deployment', async ({page}) => {
  const {calls, errors} = await setup(page, 'package-bearer');
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  await page.getByLabel('Connection name', {exact: true}).fill('My Snowflake MCP');
  await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({
    name: 'snowflake-mcp-runtime.zip', mimeType: 'application/zip', buffer: Buffer.from('PK\x03\x04snowflake-package'),
  });
  await expect(page.getByLabel('Snowflake account', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: /^Authentication method/})).toHaveCount(0);
  await page.getByRole('button', {name: 'Upload and deploy package', exact: true}).click();
  const payload = calls.find(c => c.path === '/api/admin/mcp/packages')!.body;
  expect(Object.keys(payload).sort()).toEqual(['filename', 'idempotency_key', 'name', 'size', 'source_digest']);
  await page.getByRole('button', {name: 'Use this MCP package', exact: true}).click();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Set up user sign-in (3LO)', exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Set up service access (2LO)', exact: true})).toHaveCount(0);
  await expect(page.getByText('https://python.example.com/mcp/' + 'c'.repeat(32), {exact: true})).toBeVisible();
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'Service credentials (OAuth 2LO)', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Set up service access (2LO)', exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Set up user sign-in (3LO)', exact: true})).toHaveCount(0);
  expect(errors).toEqual([]);
});

for (const scenario of ['package-discovery-failed', 'package-discovery-legacy']) {
  test(`package discovery failure shows the retained configuration and recovery guidance: ${scenario}`, async ({page}, testInfo) => {
    const {calls, errors} = await setup(page, scenario);
    await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
    await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({
      name: 'snowflake-mcp-runtime.zip', mimeType: 'application/zip', buffer: Buffer.from('PK\x03\x04snowflake-package'),
    });
    await page.getByRole('button', {name: 'Upload and deploy package', exact: true}).click();
    await expect(page.getByText(/The Runtime was deployed, but MCP tool discovery failed/)).toBeVisible();
    await expect(page.getByText(/Put your server's non-secret startup configuration in the package/)).toBeVisible();
    await expect(page.getByRole('button', {name: 'Use this MCP package', exact: true})).toHaveCount(0);
    await page.reload();
    await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
    await page.getByRole('button', {name: 'Add MCP connection', exact: true}).click();
    await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
    await expect(page.getByText(/The Runtime was deployed, but MCP tool discovery failed/)).toBeVisible();
    expect(calls.filter(c => c.path === '/api/admin/mcp/packages')).toHaveLength(1);
    await page.screenshot({path: testInfo.outputPath('package-discovery-recovery.png'), fullPage: true, animations: 'disabled'});
    await page.getByRole('button', {name: 'Choose another package', exact: true}).click();
    await expect(page.getByLabel('MCP package ZIP', {exact: true})).toBeVisible();
    await expect(page.getByText('Package connection', {exact: true})).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test('the complete package form fits mobile without provider settings', async ({page}, testInfo) => {
  const {errors} = await setup(page, 'entry');
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  await page.getByLabel('MCP package ZIP', {exact: true}).scrollIntoViewIfNeeded();
  await expect(page.getByLabel('MCP package ZIP', {exact: true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('complete-package-mobile.png'), fullPage: true, animations: 'disabled'});
  await expect(page.getByLabel('Snowflake account', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: /^Authentication method/})).toHaveCount(0);
  expect(errors).toEqual([]);
});

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

for (const mode of [
  {label: 'User sign-in (OAuth 3LO)', heading: 'Set up user sign-in (3LO)', authorization: true},
  {label: 'Service credentials (OAuth 2LO)', heading: 'Set up service access (2LO)', authorization: false},
]) {
  test(`creation has a separate onboarding step for ${mode.label}`, async ({page}, testInfo) => {
    const {calls, errors} = await setup(page);
    await page.getByRole('button', {name: /^Authentication method/}).click();
    await page.getByRole('option', {name: mode.label, exact: true}).click();
    await expect(page.getByRole('heading', {name: mode.heading, exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: /^OAuth grant/})).toHaveCount(0);
    await expect(page.getByLabel('Authorization endpoint', {exact: true})).toHaveCount(mode.authorization ? 1 : 0);
    await expect(page.getByLabel('Token endpoint', {exact: true})).toHaveCount(mode.authorization ? 1 : 0);
    await expect(page.getByLabel('OAuth discovery URL', {exact: true})).toHaveCount(mode.authorization ? 0 : 1);
    await page.screenshot({path: testInfo.outputPath('oauth-step.png'), fullPage: true});
    expect(calls).toEqual([]);
    expect(errors).toEqual([]);
  });
}

test('generic user OAuth onboarding references an existing provider and reviews supplied schemas', async ({page}) => {
  const {calls, errors} = await setup(page, 'oauth');
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  await page.getByRole('button', {name: /^OAuth provider setup/}).click();
  await page.getByRole('option', {name: 'Existing provider', exact: true}).click();
  await page.getByLabel('OAuth provider ARN', {exact: true}).fill(
    'arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/customer-mcp-oauth-data');
  await page.getByLabel('OAuth scopes', {exact: true}).fill('read');
  await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  await expect(page.getByText(/If your provider already allows this exact URL/)).toBeVisible();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  const tools = [{name: 'list_datasets', description: 'Discover datasets.', inputSchema: {type: 'object', properties: {}}}];
  await page.getByRole('button', {name: 'Paste tool definitions instead', exact: true}).click();
  await page.getByLabel('MCP tool schema JSON', {exact: true}).fill(JSON.stringify({tools}));
  await page.getByRole('button', {name: 'Connect and review', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Review supplied tools', exact: true})).toBeVisible();
  expect(calls[0].body.scopes).toEqual(['read']);
  expect(calls[1].body.tool_schema).toEqual(tools);
  expect(calls[1].body.connection_id).toBe('user-oauth');
  expect(JSON.stringify(calls)).not.toContain('client_secret');
  expect(errors).toEqual([]);
});

test('saved user OAuth cannot submit an empty tool definition', async ({page}, testInfo) => {
  const {calls, errors} = await setup(page, 'oauth');
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  await page.getByRole('button', {name: /^OAuth provider setup/}).click();
  await page.getByRole('option', {name: 'Existing provider', exact: true}).click();
  await page.getByLabel('OAuth provider ARN', {exact: true}).fill(
    'arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/customer-mcp-oauth-data');
  await page.getByLabel('OAuth scopes', {exact: true}).fill('read');
  await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await expect(page.getByText('Load the server’s tool definitions to continue.', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Paste tool definitions instead', exact: true}).click();
  await expect(page.getByLabel('MCP tool schema JSON', {exact: true})).toBeEmpty();
  await page.screenshot({path: testInfo.outputPath('saved-oauth-missing-tools.png'), fullPage: true});
  await expect(page.getByRole('button', {name: 'Connect and review', exact: true})).toBeDisabled();
  expect(calls.filter(call => call.path === '/api/admin/mcp/onboarding')).toEqual([]);
  expect(errors).toEqual([]);
});

async function saveUserOAuth(page: Page) {
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  await page.getByRole('button', {name: /^OAuth provider setup/}).click();
  await page.getByRole('option', {name: 'Existing provider', exact: true}).click();
  await page.getByLabel('OAuth provider ARN', {exact: true}).fill(
    'arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/customer-mcp-oauth-data');
  await page.getByLabel('OAuth scopes', {exact: true}).fill('read');
  await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
}

const snowflakeServerSpec = {
  version: 1,
  tools: [{
    title: 'Query permitted Snowflake data', name: 'query_sql', type: 'SYSTEM_EXECUTE_SQL',
    description: "Run read-only SQL against objects allowed by the signed-in Snowflake user's reader role. Use fully qualified names and return only the data needed for the question.",
    config: {read_only: true, warehouse: 'COMPUTE_WH'},
  }],
};

test('pasted Snowflake server_spec becomes callable SQL tools and publishes in the wizard', async ({page}, testInfo) => {
  const {calls, errors} = await setup(page, 'oauth');
  await saveUserOAuth(page);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Paste tool definitions instead', exact: true}).click();
  await page.getByLabel('MCP tool schema JSON', {exact: true}).fill(JSON.stringify(snowflakeServerSpec));
  await expect(page.getByRole('heading', {name: '1 tool definition loaded', exact: true})).toBeVisible();
  await expect(page.getByText('Snowflake definition ready', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Connect and review', exact: true})).toBeEnabled();
  await page.screenshot({path: testInfo.outputPath('snowflake-spec-paste-desktop.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await expect(page.getByRole('heading', {name: '1 tool definition loaded', exact: true})).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('snowflake-spec-paste-mobile.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Connect and review', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Review supplied tools', exact: true})).toBeVisible();
  const onboarding = calls.filter(call => call.path === '/api/admin/mcp/onboarding');
  expect(onboarding).toHaveLength(1);
  expect(onboarding[0].body.tool_schema).toEqual([{
    name: 'query_sql', description: snowflakeServerSpec.tools[0].description,
    inputSchema: {type: 'object', properties: {sql: {description: 'Single SQL query to execute.', type: 'string'}}},
  }]);
  expect(onboarding[0].body.tool_schema[0]).not.toHaveProperty('config');
  expect(onboarding[0].body.tool_schema[0].inputSchema.properties).not.toHaveProperty('warehouse');
  await page.getByRole('checkbox', {name: 'query_sql', exact: true}).check();
  await page.getByRole('button', {name: 'Approve and publish', exact: true}).click();
  await expect(page.getByText('Connection published', {exact: true})).toBeVisible();
  expect(calls.filter(call => call.path.endsWith('/publish'))).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('Snowflake definition file rejects unsupported types and versions without partial submission', async ({page}) => {
  const {calls, errors} = await setup(page, 'oauth');
  await saveUserOAuth(page);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  for (const [document, message] of [
    [{...snowflakeServerSpec, version: 2}, 'Snowflake server specification version must be 1.'],
    [{tools: snowflakeServerSpec.tools}, 'Snowflake server specification version must be 1.'],
    [{...snowflakeServerSpec, tools: [...snowflakeServerSpec.tools,
      {name: 'custom_lookup', type: 'GENERIC', identifier: 'DEMO.DATA.LOOKUP'}]},
    'Snowflake tool "custom_lookup" uses type "GENERIC", which Studio cannot convert yet.'],
    [{...snowflakeServerSpec, tools: [snowflakeServerSpec.tools[0], snowflakeServerSpec.tools[0]]},
    'Each tool must have a unique name.'],
    [{...snowflakeServerSpec, tools: [{...snowflakeServerSpec.tools[0], inputSchema: {type: 'array'}}]},
    'needs an inputSchema with type "object".'],
  ] as const) {
    await page.locator('input[type="file"]').setInputFiles({
      name: 'server-spec.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)),
    });
    await expect(page.getByText(message, {exact: false}).first()).toBeVisible();
    await expect(page.getByRole('button', {name: 'Connect and review', exact: true})).toBeDisabled();
    await expect(page.getByRole('heading', {name: /tool definitions? loaded/})).toHaveCount(0);
    expect(calls.filter(call => call.path === '/api/admin/mcp/onboarding')).toEqual([]);
  }
  await page.locator('input[type="file"]').setInputFiles({
    name: 'server-spec.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(snowflakeServerSpec)),
  });
  await expect(page.getByRole('heading', {name: '1 tool definition loaded', exact: true})).toBeVisible();
  await expect(page.getByText('Snowflake definition ready', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Connect and review', exact: true}).click();
  expect(calls.find(call => call.path === '/api/admin/mcp/onboarding')?.body.tool_schema[0].name).toBe('query_sql');
  expect(errors).toEqual([]);
});

test('tool definition import validates before connecting and publishes within the same wizard', async ({page}, testInfo) => {
  const {calls, errors} = await setup(page, 'oauth');
  await saveUserOAuth(page);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  const tools = [{name: 'list_datasets', description: 'Discover datasets.', inputSchema: {type: 'object', properties: {}}}];
  for (const [text, message] of [
    ['{', 'This is not valid JSON.'],
    [JSON.stringify({tools: []}), 'Use a server definition or tools/list response containing a tools array with 1–100 tools.'],
    [JSON.stringify({tools: [tools[0], tools[0]]}), 'Each tool must have a unique name.'],
    [JSON.stringify({tools: [{...tools[0], inputSchema: {type: 'array'}}]}), 'needs an inputSchema with type "object".'],
    [JSON.stringify({tools: [{...tools[0], inputSchema: {type: 'object', $ref: 'https://other.example.com/schema'}}]}), 'Tool schemas cannot reference external documents.'],
  ]) {
    await page.locator('input[type="file"]').setInputFiles({name: 'tools.json', mimeType: 'application/json', buffer: Buffer.from(text)});
    await expect(page.getByText(message, {exact: false}).first()).toBeVisible();
    await expect(page.getByRole('button', {name: 'Connect and review', exact: true})).toBeDisabled();
    expect(calls.filter(call => call.path === '/api/admin/mcp/onboarding')).toEqual([]);
  }
  await page.locator('input[type="file"]').setInputFiles({
    name: 'tools.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({jsonrpc: '2.0', id: 1, result: {tools}})),
  });
  await expect(page.getByRole('heading', {name: '1 tool definition loaded', exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Connect and review', exact: true})).toBeEnabled();
  await expect(page.getByLabel('MCP tool schema JSON', {exact: true})).toBeHidden();
  await page.screenshot({path: testInfo.outputPath('tools-import-desktop.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await expect(page.getByRole('button', {name: /^Authentication connection/})).toContainText('User data OAuth');
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await expect(page.getByRole('heading', {name: '1 tool definition loaded', exact: true})).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('tools-import-mobile.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Connect and review', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Review supplied tools', exact: true})).toBeVisible();
  await page.getByRole('checkbox', {name: 'list_datasets', exact: true}).check();
  await page.getByRole('button', {name: 'Approve and publish', exact: true}).click();
  await expect(page.getByText('Connection published', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Done', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Registered MCP connections', exact: true})).toBeVisible();
  expect(calls.filter(call => call.path === '/api/admin/mcp/onboarding')).toHaveLength(1);
  expect(calls.find(call => call.path === '/api/admin/mcp/onboarding')?.body.tool_schema).toEqual(tools);
  expect(calls.filter(call => call.path.endsWith('/publish'))).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('uploaded OAuth package keeps discovered tools through authentication and publication', async ({page}) => {
  const {calls, errors} = await setup(page, 'package-bearer');
  await page.getByRole('radio', {name: 'Upload MCP package (.zip)', exact: true}).check();
  await page.getByLabel('MCP package ZIP', {exact: true}).setInputFiles({
    name: 'complete.zip', mimeType: 'application/zip', buffer: Buffer.from('PK\x03\x04complete-package'),
  });
  await page.getByRole('button', {name: 'Upload and deploy package', exact: true}).click();
  await page.getByRole('button', {name: 'Use this MCP package', exact: true}).click();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Use this MCP package', exact: true})).toBeVisible();
  await expect(page.getByLabel('MCP package ZIP', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await saveUserOAuth(page);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await expect(page.getByText('Tools loaded from your package', {exact: true})).toBeVisible();
  await expect(page.getByRole('cell', {name: 'package_greeting', exact: true})).toBeVisible();
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await expect(page.getByLabel('MCP tool schema JSON', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Connect and review', exact: true}).click();
  await page.getByRole('checkbox', {name: 'package_greeting', exact: true}).check();
  await page.getByRole('button', {name: 'Approve and publish', exact: true}).click();
  await expect(page.getByText('Connection published', {exact: true})).toBeVisible();
  expect(calls.filter(call => call.path === '/api/admin/mcp/packages')).toHaveLength(1);
  expect(calls.find(call => call.path === '/api/admin/mcp/onboarding')?.body).toMatchObject({
    connection_id: 'user-oauth', endpoint: 'https://python.example.com/mcp/' + 'c'.repeat(32),
    tool_schema: [{name: 'package_greeting', inputSchema: {type: 'object', properties: {}}}],
  });
  expect(errors).toEqual([]);
});

test('the server step catches a duplicate endpoint and opens its existing registration', async ({page}) => {
  const {calls, errors} = await setup(page, 'management');
  await page.getByRole('button', {name: 'Add MCP connection', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Next', exact: true})).toBeDisabled();
  await expect(page.getByRole('button', {name: /^Authentication method/})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Add authentication connection', exact: true})).toHaveCount(0);
  await page.getByLabel('Connection name', {exact: true}).fill('New connection name');
  await page.getByLabel('MCP endpoint URL', {exact: true}).fill('http://data.example.com/mcp');
  await expect(page.getByText('Enter an HTTPS URL without embedded credentials or a fragment.', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Next', exact: true})).toBeDisabled();
  await page.getByLabel('MCP endpoint URL', {exact: true}).fill('https://data.example.com/mcp');
  await expect(page.getByText('This connection is already registered', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Next', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: 'View existing connection', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Company data', exact: true})).toBeVisible();
  await expect(page.getByText('Connection published', {exact: true})).toBeVisible();
  expect(calls).toEqual([]);
  expect(errors).toEqual([]);
});

test('a deployed package can resume setup without another ZIP or deployment', async ({page}, testInfo) => {
  const {calls, errors} = await setup(page, 'entry');
  const runtimeEndpoint = 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/' +
    'arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fstudio_python_test-1234567890/invocations?qualifier=DEFAULT';
  const saved = {id: 'f'.repeat(32), upload_type: 'package', phase: 'READY', name: 'Saved complete server',
    filename: 'complete.zip', connection_mode: 'PACKAGE', endpoint: runtimeEndpoint,
    tools: [{name: 'list_datasets', description: 'Discover datasets.', inputSchema: {type: 'object', properties: {}}}]};
  await page.route('**/api/admin/mcp/python', route => route.fulfill({json: {items: [
    saved, {...saved, id: 'failed', name: 'Failed deployment', phase: 'FAILED'},
  ]}}));
  await page.route('**/api/admin/mcp/python/' + saved.id, route => route.fulfill({json: saved}));
  await page.getByRole('radio', {name: 'Use a deployed package', exact: true}).check();
  await expect(page.getByLabel('MCP package ZIP', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Upload and deploy package', exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: /^Saved MCP package deployments/}).click();
  await expect(page.getByRole('option', {name: /Failed deployment/})).toHaveCount(0);
  await page.getByRole('option', {name: 'Saved complete server READY', exact: true}).click();
  await page.getByRole('button', {name: 'Use this MCP package', exact: true}).click();
  await expect(page.getByLabel('MCP endpoint URL', {exact: true})).toHaveValue(runtimeEndpoint);
  await page.screenshot({path: testInfo.outputPath('reuse-package-desktop.png'), fullPage: true, animations: 'disabled'});
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'AWS IAM / AgentCore Runtime', exact: true}).click();
  await page.getByRole('button', {name: 'Save IAM connection', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Review discovered tools', exact: true})).toBeVisible();
  expect(calls.filter(call => call.path.includes('/packages'))).toEqual([]);
  expect(calls.find(call => call.path === '/api/admin/mcp/onboarding')?.body.endpoint).toBe(runtimeEndpoint);
  expect(errors).toEqual([]);
});

for (const grant of ['AUTHORIZATION_CODE', 'CLIENT_CREDENTIALS']) {
  test(`create an OAuth provider manually with ${grant}`, async ({page}) => {
    const {calls, errors} = await setup(page);
    await page.getByRole('button', {name: /^Authentication method/}).click();
    await page.getByRole('option', {name: grant === 'AUTHORIZATION_CODE'
      ? 'User sign-in (OAuth 3LO)' : 'Service credentials (OAuth 2LO)', exact: true}).click();
    await page.getByLabel('OAuth client ID', {exact: true}).fill('manual-client');
    await page.getByLabel('OAuth client secret', {exact: true}).fill('test-client-secret-never-persist');
    if (grant === 'AUTHORIZATION_CODE') {
      await page.getByLabel('OAuth issuer', {exact: true}).fill('https://identity.example.com');
      await page.getByLabel('Authorization endpoint', {exact: true}).fill('https://identity.example.com/authorize');
      await page.getByLabel('Token endpoint', {exact: true}).fill('https://identity.example.com/token');
    } else {
      await page.getByLabel('OAuth discovery URL', {exact: true}).fill('https://identity.example.com/.well-known/openid-configuration');
    }
    await page.getByLabel('OAuth scopes', {exact: true}).fill('read');
    await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
    await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
    expect(calls[0]).toMatchObject({path: '/api/admin/mcp/credentials', body: {
      auth_type: 'OAUTH', grant_type: grant, client_id: 'manual-client', scopes: ['read'],
      secret: 'test-client-secret-never-persist', client_authentication_method: 'CLIENT_SECRET_POST',
    }});
    if (grant === 'CLIENT_CREDENTIALS') {
      expect(calls[0].body.discovery_url).toBe('https://identity.example.com/.well-known/openid-configuration');
      expect(calls[0].body).not.toHaveProperty('authorization_endpoint');
      expect(calls[0].body).not.toHaveProperty('issuer');
      expect(calls[0].body).not.toHaveProperty('token_endpoint');
      await expect(page.getByText('Check the OAuth callback', {exact: true})).toHaveCount(0);
    }
    expect(await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})))
      .not.toContain('test-client-secret-never-persist');
    await expect(page.getByLabel('OAuth client secret', {exact: true})).toHaveCount(0);
    await page.getByRole('button', {name: 'Next', exact: true}).click();
    if (grant === 'AUTHORIZATION_CODE') await page.getByRole('button', {name: 'Paste tool definitions instead', exact: true}).click();
    await expect(page.getByLabel('MCP tool schema JSON', {exact: true})).toHaveCount(grant === 'AUTHORIZATION_CODE' ? 1 : 0);
    expect(errors).toEqual([]);
  });
}

test('uncertain OAuth creation restores exact metadata and requires the secret only after a missing GET', async ({page}) => {
  const {calls} = await setup(page, 'managed-oauth-uncertain');
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  for (const [label, value] of [
    ['OAuth client ID', 'manual-client'], ['OAuth client secret', 'test-client-secret-never-persist'],
    ['OAuth issuer', 'https://identity.example.com'], ['Authorization endpoint', 'https://identity.example.com/authorize'],
    ['Token endpoint', 'https://identity.example.com/token'], ['OAuth scopes', 'read'],
  ]) await page.getByLabel(label, {exact: true}).fill(value);
  await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Check OAuth connection status', exact: true})).toBeVisible();
  const stored = await page.evaluate(() => JSON.stringify({...sessionStorage}));
  expect(stored).toContain('manual-client');
  expect(stored).not.toContain('test-client-secret-never-persist');
  await page.getByRole('button', {name: 'Check OAuth connection status', exact: true}).click();
  await page.getByLabel('OAuth client secret', {exact: true}).fill('test-client-secret-never-persist');
  await page.getByRole('button', {name: 'Retry retained OAuth request', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  expect(calls[1].body).toEqual(calls[0].body);
});

test('switching OAuth steps clears unsaved secrets and keeps a retained 2LO request in its own step', async ({page}) => {
  const {calls, errors} = await setup(page, 'managed-oauth-uncertain');
  async function select(label: string) {
    await page.getByRole('button', {name: /^Authentication method/}).click();
    await page.getByRole('option', {name: label, exact: true}).click();
  }
  await select('User sign-in (OAuth 3LO)');
  await page.getByLabel('OAuth client secret', {exact: true}).fill('unsaved-user-secret');
  await select('Service credentials (OAuth 2LO)');
  await expect(page.getByLabel('OAuth client secret', {exact: true})).toBeEmpty();
  await page.getByLabel('OAuth client ID', {exact: true}).fill('service-client');
  await page.getByLabel('OAuth discovery URL', {exact: true}).fill('https://identity.example.com/.well-known/openid-configuration');
  await page.getByLabel('OAuth client secret', {exact: true}).fill('service-secret-never-retain');
  await page.getByLabel('OAuth scopes', {exact: true}).fill('read');
  await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Check OAuth connection status', exact: true})).toBeVisible();
  await select('User sign-in (OAuth 3LO)');
  await expect(page.getByText('Saved OAuth request needs reconciliation', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Save OAuth connection', exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Check OAuth connection status', exact: true})).toHaveCount(0);
  expect(calls).toHaveLength(1);
  await page.reload();
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('button', {name: 'Add MCP connection', exact: true}).click();
  await page.getByLabel('Connection name', {exact: true}).fill('Company data');
  await page.getByLabel('MCP endpoint URL', {exact: true}).fill('https://data.example.com/mcp');
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await select('Service credentials (OAuth 2LO)');
  await page.getByRole('button', {name: 'Check OAuth connection status', exact: true}).click();
  await page.getByLabel('OAuth client secret', {exact: true}).fill('service-secret-never-retain');
  await page.getByRole('button', {name: 'Retry retained OAuth request', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  expect(calls).toHaveLength(2);
  expect(calls[1].body).toEqual(calls[0].body);
  expect(calls[1].body.grant_type).toBe('CLIENT_CREDENTIALS');
  expect(JSON.stringify(calls)).not.toContain('unsaved-user-secret');
  expect(await page.evaluate(() => JSON.stringify({...sessionStorage}))).not.toContain('service-secret-never-retain');
  expect(errors).toEqual([]);
});

test('legacy retained 2LO metadata remains unchanged after the onboarding steps are separated', async ({page}) => {
  const {calls} = await setup(page);
  const legacy = {auth_type: 'OAUTH', name: 'Existing service OAuth', endpoint: 'https://data.example.com/mcp',
    grant_type: 'CLIENT_CREDENTIALS', client_id: 'legacy-client', issuer: 'https://identity.example.com',
    authorization_endpoint: 'https://identity.example.com/authorize', token_endpoint: 'https://identity.example.com/token',
    client_authentication_method: 'CLIENT_SECRET_POST', scopes: ['read'], idempotency_key: 'legacy-oauth-request-0001'};
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  const storageKey = 'mcp-gateway-oauth-reference:admin:platform';
  await page.evaluate(({key, value}) => sessionStorage.setItem(key, JSON.stringify(value)), {key: storageKey, value: legacy});
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'Service credentials (OAuth 2LO)', exact: true}).click();
  await page.getByRole('button', {name: 'Check OAuth connection status', exact: true}).click();
  await page.getByLabel('OAuth client secret', {exact: true}).fill('legacy-secret');
  await page.getByRole('button', {name: 'Retry retained OAuth request', exact: true}).click();
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toEqual({...legacy, secret: 'legacy-secret'});
});

test('Runtime IAM onboarding needs no secret and retains an uncertain request unchanged', async ({page}) => {
  const {calls, errors} = await setup(page, 'iam-uncertain');
  const endpoint = 'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/' +
    encodeURIComponent('arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/customer_mcp-AbCdEf1234') +
    '/invocations?qualifier=DEFAULT';
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await page.getByLabel('MCP endpoint URL', {exact: true}).fill(endpoint);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: /^Authentication method/}).click();
  await page.getByRole('option', {name: 'AWS IAM / AgentCore Runtime', exact: true}).click();
  await expect(page.getByLabel('API key or PAT', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Save IAM connection', exact: true}).click();
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await page.getByLabel('Connection name', {exact: true}).fill('Changed after uncertain request');
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Check IAM connection status', exact: true}).click();
  await page.getByRole('button', {name: 'Retry retained IAM request', exact: true}).click();
  await expect(page.getByText('Authentication saved', {exact: true})).toBeVisible();
  expect(calls).toHaveLength(2);
  expect(calls[0].body).toEqual({name: 'Company data IAM', endpoint, idempotency_key: expect.any(String)});
  expect(calls[1].body).toEqual(calls[0].body);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
  expect(calls[2].body).toMatchObject({endpoint, connection_id: 'runtime-iam'});
  expect(calls.filter(c => c.path.endsWith('/publish'))).toHaveLength(0);
  expect(errors).toEqual([]);
});

test('standalone saved credentials can be edited and deleted without retaining the secret', async ({page}) => {
  const {calls, errors} = await setup(page, 'management');
  await page.getByRole('button', {name: 'Manage saved authentication', exact: true}).click();
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

test('saved authentication supports generic OAuth provider references', async ({page}) => {
  const {calls, errors} = await setup(page, 'management');
  await page.getByRole('button', {name: 'Manage saved authentication', exact: true}).click();
  await page.getByRole('button', {name: 'Add authentication connection', exact: true}).click();
  await page.getByLabel('Authentication name', {exact: true}).fill('User data');
  await page.getByLabel('Authentication endpoint URL', {exact: true}).fill('https://data.example.com/mcp');
  await page.getByRole('button', {name: /New authentication method/}).click();
  await expect(page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true})).toBeVisible();
  await expect(page.getByRole('option', {name: 'Service credentials (OAuth 2LO)', exact: true})).toBeVisible();
  await page.getByRole('option', {name: 'User sign-in (OAuth 3LO)', exact: true}).click();
  await page.getByRole('button', {name: /^OAuth provider setup/}).click();
  await page.getByRole('option', {name: 'Existing provider', exact: true}).click();
  await page.getByLabel('OAuth provider ARN', {exact: true}).fill(
    'arn:aws:bedrock-agentcore:us-east-1:123456789012:token-vault/default/oauth2credentialprovider/customer-mcp-oauth-data');
  await page.getByLabel('OAuth scopes', {exact: true}).fill('read');
  await page.getByRole('button', {name: 'Save OAuth connection', exact: true}).click();
  await expect(page.getByText('Authentication connection added.', {exact: true})).toBeVisible();
  expect(calls[0].path).toBe('/api/admin/mcp/oauth-credentials');
  expect(calls[0].body.scopes).toEqual(['read']);
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
  await page.getByRole('button', {name: 'Next', exact: true}).click();
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
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Endpoint configuration changed', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Connect and discover', exact: true})).toBeEnabled();
  expect(calls).toHaveLength(1);
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await page.getByLabel('Connection name', {exact: true}).fill('Updated connection');
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
});

test('uncertain create requires GET before an explicit same-key retry', async ({page}) => {
  const {calls} = await setup(page, 'uncertain');
  await page.getByRole('button', {name: 'Next', exact: true}).click();
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
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Connect and discover', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Retry original request', exact: true})).toBeVisible();
  expect(calls).toHaveLength(1);
  await page.getByRole('button', {name: 'Retry original request', exact: true}).click();
  await expect(page.getByText('Review discovered tools', {exact: true})).toBeVisible();
  expect(calls[1]).toEqual({path: '/api/admin/mcp/onboarding/remote-1/retry', body: {job_id: 'job-1'}});
});
