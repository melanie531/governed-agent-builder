import {test, expect, type Page} from '@playwright/test';

async function setup(page: Page) {
  const calls: {path: string; body: any}[] = [], errors: string[] = [];
  let connected = false;
  page.on('pageerror', error => errors.push(error.message));
  page.context().on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path.startsWith('/api/')) calls.push({path, body: request.postDataJSON()});
  });
  await page.context().route('**/studio-config.json', r => r.fulfill({json: {hosted: true, journey_enabled: true}}));
  await page.context().route('**/auth/verification/status', r => r.fulfill({status: 401, json: {detail: 'No pending verification'}}));
  await page.context().route('**/api/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === '/api/me') return route.fulfill({json: {
      persona: {id: 'user-one', name: 'Research user', role: 'business', workspace: 'research', workspace_name: 'Research studio'},
      csrf: 'test-csrf',
    }});
    if (path === '/api/agents') return route.fulfill({json: []});
    if (path === '/api/mcp/user-connections') return route.fulfill({json: {
      items: [{id: 'customer-runtime', name: 'Customer data Runtime', phase: connected ? 'CONNECTED' : 'NOT_CONNECTED'}],
    }});
    if (path === '/api/mcp/user-connections/customer-runtime/authorize') return route.fulfill({json: {
      id: 'flow-one', server_id: 'customer-runtime', name: 'Customer data Runtime', phase: 'CONSENT_REQUIRED',
      authorization_url: 'https://provider.example.com/authorize?state=' + 's'.repeat(43),
    }});
    if (path === '/api/mcp/user-connections/complete') {
      connected = true;
      return route.fulfill({json: {id: 'flow-one', server_id: 'customer-runtime', name: 'Customer data Runtime', phase: 'CONNECTED'}});
    }
    return route.fulfill({status: 404, json: {detail: 'Unexpected route'}});
  });
  return {calls, errors};
}

test('a business user can start provider consent through generic My connections', async ({page}) => {
  const {calls, errors} = await setup(page);
  await page.goto('/');
  await page.getByRole('link', {name: 'My connections', exact: true}).click();
  await expect(page.getByText('Customer data Runtime', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Connect account', exact: true}).click();
  await expect(page.getByRole('link', {name: 'Continue to provider', exact: true})).toHaveAttribute('href', /^https:\/\/provider\.example\.com\/authorize/);
  expect(calls).toHaveLength(1);
  expect(calls[0].body).toMatchObject({force_authentication: false});
  expect(calls[0].body.idempotency_key).toBeTruthy();
  expect(errors).toEqual([]);
});

test('the callback completes for the signed-in user without another button', async ({page}) => {
  const {calls, errors} = await setup(page);
  const state = 's'.repeat(43), session = 'urn:ietf:params:oauth:request_uri:native-session';
  await page.addInitScript(() => sessionStorage.setItem('mcp-user-authorization:user-one', JSON.stringify({
    server_id: 'customer-runtime', idempotency_key: 'retained-request-key', force_authentication: false,
  })));
  await page.goto('/oauth/callback?' + new URLSearchParams({state, session_id: session}));
  await expect(page.getByText('Authorization complete. Return to Studio or close this tab.', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Complete authorization', exact: true})).toHaveCount(0);
  expect(calls).toEqual([{path: '/api/mcp/user-connections/complete', body: {state, session_uri: session}}]);
  expect(new URL(page.url()).search).toBe('');
  expect(errors).toEqual([]);
});

test('a native Gateway callback keeps Cognito sign-in and binds its session without a custom state', async ({page}) => {
  const {calls, errors} = await setup(page);
  const session = 'urn:ietf:params:oauth:request_uri:gateway-session';
  await page.goto('/oauth/callback?' + new URLSearchParams({session_id: session}));
  await expect(page.getByText('Authorization complete. Return to Studio or close this tab.', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Return to My connections'})).toBeVisible();
  await page.waitForTimeout(1000);
  expect(page.isClosed()).toBe(false);
  expect(calls).toEqual([{path: '/api/mcp/user-connections/complete', body: {session_uri: session}}]);
  expect(errors).toEqual([]);
});

test('an expired Studio session preserves the callback until the user signs back in', async ({page}) => {
  const {calls, errors} = await setup(page);
  await page.route('**/api/me', route => route.fulfill({status: 401, json: {detail: 'Sign in'}}));
  const state = 's'.repeat(43), session = 'urn:ietf:params:oauth:request_uri:retained-session';
  await page.goto('/oauth/callback?' + new URLSearchParams({state, session_id: session}));
  await expect(page.getByRole('button', {name: 'Sign in / Open Studio'})).toBeVisible();
  expect(calls).toHaveLength(0);
  await page.unroute('**/api/me');
  await page.reload();
  await expect(page.getByText('Authorization complete. Return to Studio or close this tab.', {exact: true})).toBeVisible();
  expect(calls).toEqual([{path: '/api/mcp/user-connections/complete', body: {state, session_uri: session}}]);
  expect(errors).toEqual([]);
});

test('an agent opens consent in a new tab and resumes only its first-tool question once', async ({page}) => {
  const {calls, errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'User data agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [], jobs: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'NOT_STARTED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
    last_invocation: {id: 'query-one', phase: 'AUTHORIZATION_REQUIRED', authorization_flow_id: 'flow-one',
      resumable: true, input: 'What data can I read?'}};
  let connected = false;
  let resumes = 0;
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.context().route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one/resume', route => {
    resumes++;
    detail.last_invocation = {id: 'query-two', phase: 'SUCCEEDED', output: 'Read-only result'} as typeof detail.last_invocation;
    return route.fulfill({json: {job_id: 'query-two'}});
  });
  await page.context().route('https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?*',
    route => route.fulfill({contentType: 'text/html', body: '<h1>Provider sign-in</h1>'}));
  await page.context().route('**/api/mcp/user-connections/complete', route => {
    connected = true;
    return route.fulfill({json: {id: 'flow-one', server_id: 'customer-runtime',
      name: 'User data', phase: 'CONNECTED'}});
  });
  await page.context().route('**/api/mcp/user-connections/flows/flow-one', route => route.fulfill({json: {
    id: 'flow-one', name: 'User data', phase: connected ? 'CONNECTED' : 'CONSENT_REQUIRED',
    authorization_url: 'https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=synthetic',
  }}));
  await page.goto('/');
  await page.getByRole('link', {name: 'User data agent', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Open My connections'})).toHaveCount(0);
  await expect(page.getByRole('link', {name: 'Open provider sign-in', exact: true})).toBeVisible();
  await expect(page.getByRole('link', {name: 'Open provider sign-in', exact: true})).toHaveAttribute('target', '_blank');
  expect(calls).toHaveLength(0);
  const popupPromise = page.waitForEvent('popup');
  await page.getByRole('link', {name: 'Open provider sign-in', exact: true}).click();
  const popup = await popupPromise;
  await expect(popup.getByText('Provider sign-in')).toBeVisible();
  await page.getByRole('tab', {name: 'API access'}).click();
  await popup.goto(new URL('/oauth/callback?session_id=urn:ietf:params:oauth:request_uri:gateway-session', page.url()).href);
  await expect(popup.getByText('Authorization complete. Return to Studio or close this tab.', {exact: true})).toBeVisible();
  await popup.close();
  await expect.poll(() => resumes).toBe(1);
  await page.getByRole('tab', {name: 'Chat', exact: true}).click();
  await expect(page.getByText('Read-only result', {exact: true})).toBeVisible();
  expect(calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('sending a normal question opens Snowflake sign-in, closes it after consent, then continues the answer', async ({page}) => {
  const {calls, errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'NOT_STARTED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
    gateway_force_auth_supported: true, gateway_reauthorization: [{server_id: 'customer-runtime', due: true}],
    preopen_provider_tab: true,
    last_invocation: null as null | {id: string; phase: string; authorization_flow_id?: string;
      resumable?: boolean; input?: string; output?: string}};
  let connected = false;
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.context().route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one/invoke', route => {
    detail.last_invocation = {id: 'query-one', phase: 'AUTHORIZATION_REQUIRED',
      authorization_flow_id: 'flow-one', resumable: true, input: 'What tables can I read?'};
    return route.fulfill({status: 202, json: {job_id: 'query-one', conversation_id: 'conversation-one'}});
  });
  await page.route('**/api/journey/agents/agent-one/resume', route => {
    detail.last_invocation = {id: 'query-two', phase: 'SUCCEEDED', output: 'Two readable tables'};
    return route.fulfill({status: 202, json: {job_id: 'query-two', conversation_id: 'conversation-one'}});
  });
  await page.context().route('**/api/mcp/user-connections/complete', route => {
    connected = true;
    return route.fulfill({json: {id: 'flow-one', server_id: 'customer-runtime',
      name: 'Snowflake', phase: 'CONNECTED'}});
  });
  await page.context().route('**/api/mcp/user-connections/flows/flow-one', route => route.fulfill({json: {
    id: 'flow-one', name: 'Snowflake', phase: connected ? 'CONNECTED' : 'CONSENT_REQUIRED',
    authorization_url: 'https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=synthetic',
  }}));
  await page.context().route('https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?*',
    route => route.fulfill({contentType: 'text/html', body: '<h1>Snowflake sign-in</h1>'}));
  await page.goto('/');
  await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
  await expect(page.getByRole('tab', {name: 'Chat', exact: true})).toBeVisible();
  await expect(page.getByRole('tab', {name: 'Try agent', exact: true})).toHaveCount(0);
  await page.getByRole('textbox', {name: 'Your question'}).fill('What tables can I read?');
  await expect(page.getByRole('button', {name: 'Run agent'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Refresh authorization status'})).toHaveCount(0);
  const popupPromise = page.waitForEvent('popup', {timeout: 5000});
  await page.getByRole('button', {name: 'Send message'}).click();
  const popup = await popupPromise;
  await expect(popup.getByText('Snowflake sign-in')).toBeVisible();
  await test.info().attach('provider-sign-in', {body: await popup.screenshot(), contentType: 'image/png'});
  await expect(page.getByRole('link', {name: 'Continue to provider', exact: true})).toHaveCount(0);
  await popup.goto(new URL('/oauth/callback?session_id=urn:ietf:params:oauth:request_uri:gateway-session', page.url()).href);
  await expect.poll(() => popup.isClosed()).toBe(true);
  await expect(page.getByText('Two readable tables', {exact: true})).toBeVisible();
  await test.info().attach('agent-dialogue-after-consent', {body: await page.screenshot(), contentType: 'image/png'});
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.getByRole('button', {name: 'Send message'})).toBeVisible();
  await page.getByRole('button', {name: 'Send message'}).scrollIntoViewIfNeeded();
  await test.info().attach('agent-dialogue-mobile', {body: await page.screenshot(), contentType: 'image/png'});
  expect(calls.filter(call => call.path.endsWith('/invoke'))).toHaveLength(1);
  expect(calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(1);
  expect(errors).toEqual([]);
});

test('the provider tab reaches Snowflake when Studio remounts while authorization is pending', async ({page}) => {
  const {errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'SKIPPED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
    gateway_force_auth_supported: true, gateway_reauthorization: [{server_id: 'customer-runtime', due: true}],
    conversation: null, last_invocation: null as null | {id: string; phase: string; authorization_flow_id?: string;
      resumable?: boolean; input?: string}};
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.context().route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one/invoke', route => {
    setTimeout(() => {
      detail.last_invocation = {id: 'query-one', phase: 'AUTHORIZATION_REQUIRED',
        authorization_flow_id: 'flow-one', resumable: true, input: 'List my databases'};
    }, 1500);
    return route.fulfill({status: 202, json: {job_id: 'query-one', conversation_id: 'conversation-one'}});
  });
  await page.context().route('**/api/mcp/user-connections/flows/flow-one', route => route.fulfill({json: {
    id: 'flow-one', server_id: 'customer-runtime', name: 'Snowflake', mode: 'gateway', phase: 'CONSENT_REQUIRED',
    authorization_url: 'https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=synthetic',
  }}));
  await page.context().route('https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?*',
    route => route.fulfill({contentType: 'text/html', body: '<h1>Snowflake sign-in</h1>'}));
  await page.goto('/');
  await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
  await page.getByRole('textbox', {name: 'Your question'}).fill('List my databases');
  const popupPromise = page.waitForEvent('popup');
  await page.getByRole('button', {name: 'Send message'}).click();
  const popup = await popupPromise;
  await expect(popup.getByText('Studio is starting a fresh provider sign-in for your question.')).toBeVisible();
  await page.reload();
  await expect(popup.getByText('Snowflake sign-in')).toBeVisible({timeout: 8000});
  expect(errors).toEqual([]);
});

for (const transition of ['in-progress callback', 'callback continuation race'] as const) {
test(`chat follows an ${transition} and reuses consent for the next message`, async ({page}) => {
  const {calls, errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
    foundation_name: 'Research', tools: [], skills: [], dataset: []};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'SKIPPED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', gateway_force_auth_supported: true,
    gateway_reauthorization: [{server_id: 'customer-runtime', due: true, due_at: 0}],
    conversation: {id: 'conversation-one', messages: []},
    last_invocation: {id: 'query-one', phase: 'AUTHORIZATION_REQUIRED', authorization_flow_id: 'flow-one',
      resumable: true} as {id: string; phase: string; authorization_flow_id?: string; resumable?: boolean; output?: string}};
  // The public API maps the callback's transient COMPLETING phase to NEEDS_CHECK.
  let flowPhase = transition === 'in-progress callback' ? 'NEEDS_CHECK' : 'CONNECTED', flowReads = 0;
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/mcp/user-connections/flows/flow-one', route => {
    flowReads++;
    return route.fulfill({json: {id: 'flow-one', name: 'Snowflake', phase: flowPhase}});
  });
  await page.route('**/api/journey/agents/agent-one/invoke', route => {
    detail.last_invocation = {id: 'query-three', phase: 'SUCCEEDED', output: 'Second message answered'};
    return route.fulfill({status: 202, json: {job_id: 'query-three', conversation_id: 'conversation-one'}});
  });
  await page.route('**/api/journey/agents/agent-one/resume', route => {
    detail.gateway_reauthorization = [{server_id: 'customer-runtime', due: false, due_at: Date.now() / 1000 + 3600}];
    detail.last_invocation = {id: 'query-two', phase: 'SUCCEEDED', output: 'Original message answered'};
    return route.fulfill({status: 409, json: {detail: 'Concurrent governance update; reload and retry'}});
  });
  await page.goto('/');
  await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
  await expect.poll(() => flowReads).toBeGreaterThanOrEqual(2);
  // The callback completes on the server after both chat and consent polls saw
  // NEEDS_CHECK. No opener message, focus change or manual refresh follows.
  flowPhase = 'CONNECTED';
  detail.gateway_reauthorization = [{server_id: 'customer-runtime', due: false, due_at: Date.now() / 1000 + 3600}];
  detail.last_invocation = {id: 'query-two', phase: 'SUCCEEDED', output: 'Original message answered'};
  await expect(page.getByText('Original message answered', {exact: true})).toBeVisible({timeout: 8000});
  await expect(page.getByText('Provider authorization', {exact: true})).toHaveCount(0);
  const popups: Page[] = [];
  page.on('popup', popup => popups.push(popup));
  await page.getByRole('textbox', {name: 'Your question'}).fill('Now list the schemas');
  await page.getByRole('button', {name: 'Send message'}).click();
  await expect(page.getByText('Second message answered', {exact: true})).toBeVisible();
  expect(popups).toHaveLength(0);
  expect(calls.filter(call => call.path.endsWith('/invoke'))).toHaveLength(1);
  expect(calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(transition === 'in-progress callback' ? 0 : 1);
  expect(errors).toEqual([]);
});
}

for (const scenario of ['new Studio session', 'expired authorization', 'stale page after one hour'] as const) {
  test(`sending a question requests native Gateway 3LO during the first tool call for a ${scenario}`, async ({page}) => {
    const {calls, errors} = await setup(page);
    const conversationId = 'c'.repeat(32);
    const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
      foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
      dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
    const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
      deployment: {status: 'DEPLOYED', binding: {gateway_force_auth_v1: true}}, evaluation: {status: 'NOT_STARTED', cases: [], score: null},
      readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
      gateway_force_auth_supported: true,
      gateway_reauthorization: [{server_id: 'customer-runtime', due: scenario !== 'stale page after one hour',
        due_at: scenario === 'stale page after one hour' ? Date.now() / 1000 - 1 : Date.now() / 1000 + 3600}],
      preopen_provider_tab: scenario !== 'stale page after one hour',
      conversation: scenario !== 'new Studio session' ? {id: conversationId, messages: []} : null,
      last_invocation: null as null | {id: string; phase: string; output?: string}};
    let connected = false;
    await page.route('**/api/agents', route => route.fulfill({json: [
      {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
    ]}));
    await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
    await page.context().route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
    await page.context().route('**/api/mcp/user-connections/flows/forced-flow', route => route.fulfill({json: {
      id: 'forced-flow', server_id: 'customer-runtime', name: 'Snowflake', mode: 'gateway',
      phase: connected ? 'CONNECTED' : 'CONSENT_REQUIRED',
      authorization_url: 'https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=synthetic',
    }}));
    await page.context().route('**/api/mcp/user-connections/complete', route => {
      connected = true;
      return route.fulfill({json: {id: 'forced-flow', server_id: 'customer-runtime',
        name: 'Snowflake', phase: 'CONNECTED', mode: 'gateway'}});
    });
    await page.route('**/api/journey/agents/agent-one/invoke', route => {
      detail.last_invocation = {id: 'query-one', phase: 'AUTHORIZATION_REQUIRED',
        authorization_flow_id: 'forced-flow', resumable: true, input: 'List my databases'};
      return route.fulfill({status: 202, json: {job_id: 'query-one', conversation_id: conversationId}});
    });
    await page.route('**/api/journey/agents/agent-one/resume', route => {
      detail.last_invocation = {id: 'query-two', phase: 'SUCCEEDED', output: 'Verified query response'};
      return route.fulfill({status: 202, json: {job_id: 'query-two', conversation_id: conversationId}});
    });
    await page.context().route('https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?*',
      route => route.fulfill({contentType: 'text/html', body: '<h1>Snowflake sign-in</h1>'}));
    await page.goto('/');
    await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
    await page.getByRole('textbox', {name: 'Your question'}).fill('List my databases');
    const popupPromise = page.waitForEvent('popup', {timeout: 5000});
    await page.getByRole('button', {name: 'Send message'}).click();
    const popup = await popupPromise;
    await expect(popup.getByText('Snowflake sign-in')).toBeVisible();
    expect(calls.filter(call => call.path.endsWith('/invoke'))).toHaveLength(1);
    expect(calls.filter(call => call.path.endsWith('/authorize'))).toHaveLength(0);
    await popup.goto(new URL('/oauth/callback?session_id=urn:ietf:params:oauth:request_uri:gateway-session', page.url()).href);
    await expect.poll(() => popup.isClosed()).toBe(true);
    await expect(page.getByText('Verified query response', {exact: true})).toBeVisible();
    expect(calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(1);
    expect(calls.filter(call => call.path.endsWith('/invoke'))).toHaveLength(1);
    expect(errors).toEqual([]);
  });
}

test('a new conversation in the same authorized Studio session does not open another sign-in tab', async ({page}) => {
  const {calls, errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'NOT_STARTED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
    preopen_provider_tab: false, gateway_force_auth_supported: true,
    gateway_reauthorization: [{server_id: 'customer-runtime', due: false}],
    conversation: null,
    last_invocation: null as null | {id: string; phase: string; output?: string}};
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one/invoke', route => {
    detail.last_invocation = {id: 'query-one', phase: 'SUCCEEDED', output: 'Snowflake connection failed'};
    return route.fulfill({status: 202, json: {job_id: 'query-one', conversation_id: 'conversation-one'}});
  });
  await page.goto('/');
  await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
  await page.getByRole('textbox', {name: 'Your question'}).fill('What tables can I read?');
  let popups = 0;
  page.on('popup', () => {popups++;});
  await page.getByRole('button', {name: 'Send message'}).click();
  await expect(page.getByText('Snowflake connection failed', {exact: true})).toBeVisible();
  expect(popups).toBe(0);
  expect(calls.filter(call => call.path.endsWith('/invoke'))[0].body.conversation_id).toBeNull();
  expect(calls.filter(call => call.path.endsWith('/authorize'))).toHaveLength(0);
  expect(errors).toEqual([]);
});

test('consent never replays a question when a tool already completed', async ({page}) => {
  const {calls, errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'NOT_STARTED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
    last_invocation: {id: 'query-one', phase: 'AUTHORIZATION_REQUIRED', authorization_flow_id: 'flow-one',
      resumable: false, input: 'What tables can I read?'}};
  let connected = false;
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.context().route('**/api/mcp/user-connections/complete', route => {
    connected = true;
    return route.fulfill({json: {id: 'flow-one', server_id: 'customer-runtime',
      name: 'Snowflake', phase: 'CONNECTED'}});
  });
  await page.route('**/api/mcp/user-connections/flows/flow-one', route => route.fulfill({json: {
    id: 'flow-one', name: 'Snowflake', phase: connected ? 'CONNECTED' : 'CONSENT_REQUIRED',
    authorization_url: 'https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?request_uri=synthetic',
  }}));
  await page.context().route('https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?*',
    route => route.fulfill({contentType: 'text/html', body: '<h1>Snowflake sign-in</h1>'}));
  await page.goto('/');
  await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
  await expect(page.getByRole('textbox', {name: 'Your question'})).toHaveValue('What tables can I read?');
  await expect(page.getByText('This question cannot be continued automatically', {exact: false})).toBeVisible();
  const popupPromise = page.waitForEvent('popup');
  await page.getByRole('link', {name: 'Open provider sign-in', exact: true}).click();
  const popup = await popupPromise;
  await popup.goto(new URL('/oauth/callback?session_id=urn:ietf:params:oauth:request_uri:gateway-session', page.url()).href);
  await expect(popup.getByText('Authorization complete. Return to Studio or close this tab.', {exact: true})).toBeVisible();
  await popup.close();
  await expect(page.getByText('Authorization complete. Send your question again to continue.')).toBeVisible();
  expect(calls.filter(call => call.path.endsWith('/resume'))).toHaveLength(0);
  expect(errors).toEqual([]);
});

test('a failed MCP result is visible as a tool failure, not a disconnected account claim', async ({page}) => {
  const {errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'Snowflake agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [],
    deployment: {status: 'DEPLOYED'}, evaluation: {status: 'NOT_STARTED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true,
    last_invocation: {id: 'query-one', phase: 'SUCCEEDED', output: 'I could not list your tables.',
      tool_calls: [{name: 'snowflake___list_tables', arguments: {}, status: 'error'}]}};
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.goto('/');
  await page.getByRole('link', {name: 'Snowflake agent', exact: true}).click();
  await expect(page.getByText('An MCP tool returned an error. Its result was not verified.')).toBeVisible();
  expect(errors).toEqual([]);
});

test('an old deployment consent state offers redeploy without displaying the stale provider link', async ({page}) => {
  const {errors} = await setup(page);
  const definition = {catalog_mode: 'journey', name: 'User data agent', foundation_id: 'research',
    foundation_name: 'Research', model: {id: 'model', name: 'Approved model'}, tools: [], skills: [],
    dataset: [], prompt: 'Discover readable data', success_criteria: 'Read only', component_versions: {}};
  const detail = {id: 'agent-one', current_version: 1, definition, versions: [], jobs: [],
    deployment: {status: 'AUTHORIZATION_REQUIRED', authorization_flow_id: 'old-flow'},
    evaluation: {status: 'SKIPPED', cases: [], score: null},
    readiness: {deployable: true, issues: []}, mode: 'live', user_authorization_required: true};
  await page.route('**/api/agents', route => route.fulfill({json: [
    {id: detail.id, name: definition.name, current_version: 1, foundation_id: 'research'},
  ]}));
  await page.route('**/api/agents/agent-one', route => route.fulfill({json: detail}));
  await page.route('**/api/journey/agents/agent-one', route => route.fulfill({json: detail}));
  await page.goto('/');
  await page.getByRole('link', {name: 'User data agent', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Deploy to AgentCore'})).toBeEnabled();
  await expect(page.getByText('Deploy again to finish Runtime setup.', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Send message'})).toBeDisabled();
  await expect(page.getByRole('link', {name: 'Continue to provider'})).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('an uncertain consent completion is checked explicitly without repeating the completion call', async ({page}) => {
  const {calls, errors} = await setup(page);
  let completes = 0, checks = 0;
  await page.route('**/api/mcp/user-connections/complete', route => {
    completes++;
    return route.fulfill({json: {id: 'flow-one', server_id: 'customer-runtime', name: 'Customer data Runtime', phase: 'NEEDS_CHECK'}});
  });
  await page.route('**/api/mcp/user-connections/flows/flow-one/check', route => {
    checks++;
    return route.fulfill({json: {id: 'flow-one', server_id: 'customer-runtime', name: 'Customer data Runtime', phase: 'NEEDS_CHECK'}});
  });
  await page.goto('/oauth/callback?' + new URLSearchParams({state: 's'.repeat(43), session_id: 'urn:ietf:params:oauth:request_uri:native-session'}));
  await expect(page.getByRole('button', {name: 'Check authorization result', exact: true})).toBeVisible();
  expect(completes).toBe(1); expect(checks).toBe(0);
  await page.getByRole('button', {name: 'Check authorization result', exact: true}).click();
  await expect.poll(() => checks).toBe(1);
  expect(completes).toBe(1);
  expect(calls.map(call => call.path)).toEqual([
    '/api/mcp/user-connections/complete', '/api/mcp/user-connections/flows/flow-one/check',
  ]);
  expect(errors).toEqual([]);
});
