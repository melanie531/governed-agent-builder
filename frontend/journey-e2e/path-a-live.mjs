// Path A through the hosted Studio: a business user builds an Agent from the
// platform-curated specialist template, runs it (Agent Runtime -> Gateway ->
// specialist MCP Runtime), an admin revokes the grant in the Studio UI, and
// the next run is denied. Actual Cognito + AgentCore; no intercepted responses.
// Credentials stay in process memory; no auth trace or storage-state is written.
import {chromium, expect as baseExpect} from '@playwright/test';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {setTimeout as pause} from 'node:timers/promises';

const root = resolve(import.meta.dirname, '../..');
const expect = baseExpect.configure({timeout: 45000});
const statePath = process.env.GAB_RELEASE_STATE;
if (!statePath) throw new Error('Explicit GAB_RELEASE_STATE is required');
const state = JSON.parse(readFileSync(statePath, 'utf8'));
if (!process.env.GAB_EXPECTED_ACCOUNT || state.target.account !== process.env.GAB_EXPECTED_ACCOUNT) throw new Error('Unexpected test target');
const origin = state.app.outputs.ApplicationOrigin;
const TEMPLATE = 'Risk review (specialist)';
const TOOL_ID = 'specialist-risk-analyst';
const GATEWAY_TOOL = 'risk-analyst-specialist___agent-risk-analyst';
const QUESTION = 'What is the renewal risk for our synthetic account? Ask the risk analyst specialist.';
const directory = resolve(root, 'artifacts/journey-path-a');
mkdirSync(directory, {recursive: true});
const evidence = {started: new Date().toISOString()};
const save = () => writeFileSync(resolve(directory, 'path-a-evidence.json'), JSON.stringify(evidence, null, 2), {mode: 0o600});

function credentialsFor(key) {
  const secret = spawnSync(resolve(root, '.venv/bin/python'), ['-c', `
import boto3,json,sys
s=json.load(open(sys.argv[1]))
session=boto3.Session(profile_name=s['target']['profile'],region_name=s['target']['region'])
assert session.client('sts').get_caller_identity()['Account']==s['target']['account']
ssm=session.client('ssm')
print(json.dumps({k:ssm.get_parameter(Name=s[sys.argv[2]]['parameterPrefix']+'/'+k,WithDecryption=True)['Parameter']['Value'] for k in ('username','password')}))
`, statePath, key], {encoding: 'utf8', cwd: root});
  if (secret.status !== 0) throw new Error('Could not load the scoped QA credentials');
  return JSON.parse(secret.stdout);
}

const browser = await chromium.launch({headless: true, args: ['--disable-quic']});

async function navigate(page, name) {
  const toggle = page.getByRole('button', {name: 'Open side navigation', exact: true});
  if (await toggle.isVisible()) await toggle.click();
  await page.getByRole('link', {name, exact: true}).click();
}

async function session(key, role) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1080}});
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const credentials = credentialsFor(key);
  await page.goto(origin, {waitUntil: 'domcontentloaded'});
  await page.getByRole('button', {name: 'Sign in / Open Studio', exact: true}).click();
  const landing = role === 'admin' ? 'Platform overview' : 'My agents';
  for (let attempt = 0; attempt < 2; attempt++) {
    const username = page.locator('input[name="username"]:visible');
    const password = page.locator('input[name="password"]:visible');
    await username.waitFor();
    await page.waitForLoadState('networkidle', {timeout: 15000}).catch(() => {});
    await expect.poll(async () => {
      await username.fill(credentials.username);
      await password.fill(credentials.password);
      await password.press('Tab');
      await pause(250);
      return await username.inputValue() === credentials.username
        && await password.inputValue() === credentials.password;
    }, {message: 'Cognito form hydrated and retained input', timeout: 15000}).toBe(true);
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    const outcome = await Promise.race([
      page.getByRole('heading', {name: landing, exact: true}).waitFor({timeout: 60000}).then(() => 'signed-in'),
      page.getByText('Missing email address.', {exact: true}).waitFor({timeout: 60000}).then(() => 'empty-form'),
    ]).catch(() => 'failed');
    if (outcome === 'signed-in') break;
    // Retry only Cognito's local empty-form validation, never rejected credentials.
    if (outcome !== 'empty-form' || attempt === 1) throw new Error(`Hosted Cognito ${role} sign-in did not complete`);
  }
  delete credentials.password;
  const read = async path => {
    try {
      return await page.request.get(origin + path, {maxRetries: 3, timeout: 45000});
    } catch {
      // Playwright's raw transport errors include cookie headers.
      throw new Error('Hosted status request failed after transport retries');
    }
  };
  const me = await (await read('/api/me')).json();
  if (me.persona.id !== state[key].subject || me.persona.role !== role) throw new Error(`Incorrect hosted ${role} identity`);
  console.log(`Hosted Cognito ${role} sign-in: PASS`);
  return {page, read};
}

async function waitStatus(read, agentId, field, expected) {
  const deadline = Date.now() + 900000;
  let previous;
  while (Date.now() < deadline) {
    const response = await read('/api/journey/agents/' + agentId);
    if (response.status() === 409) { await pause(1000); continue; }
    if (!response.ok()) throw new Error(`Hosted status returned HTTP ${response.status()}`);
    const detail = await response.json();
    const status = detail[field]?.status || 'NOT_REQUESTED';
    if (status !== previous) console.log(`${field}: ${status}`);
    previous = status;
    if (status === expected) return detail;
    if (['FAILED', 'ERROR', 'UNKNOWN', 'STALE', 'FAILED_QUALITY', 'DELETE_FAILED'].includes(status)) {
      throw new Error(`${field}: ${status}: ${detail[field].error || 'Review the recorded cases'}`);
    }
    await pause(10000);
  }
  throw new Error(`${field} did not complete within the test deadline`);
}

async function submitChat(page) {
  await page.getByRole('textbox', {name: 'Your question', exact: true}).fill(QUESTION);
  const submitted = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/invoke'));
  await page.getByRole('button', {name: 'Run agent', exact: true}).click();
  return await submitted;
}

let business, admin;
// The grant checkbox is controlled: it flips only after the POST and catalog reload.
async function setGrant(enabled, reason) {
  const ap = admin.page;
  const catalog = await (await admin.read('/api/admin/catalog')).json();
  const persona = catalog.personas.find(p => p.id === state.journeyQA.subject);
  const component = catalog.components.find(c => c.id === TOOL_ID);
  if (!persona || !component) throw new Error('Admin catalog lacks the QA persona or specialist component');
  const tab = ap.getByRole('tab', {name: 'Tool / skill registry', exact: true});
  if (!await tab.isVisible()) await navigate(ap, 'Foundation library');
  await tab.click();
  await ap.getByLabel('Find a capability').fill('risk analyst');
  const grant = ap.getByRole('checkbox', {name: `${persona.name} access to ${component.name}`, exact: true});
  await expect(grant).toBeEnabled();
  if (await grant.isChecked() === enabled) return null;
  await ap.getByLabel(`Grant reason for ${TOOL_ID}`).fill(reason);
  const posted = ap.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/admin/grants'));
  await grant.click();
  const response = await posted;
  if (!response.ok()) throw new Error(`Grant update failed: HTTP ${response.status()} ${await response.text()}`);
  await expect(grant).toBeChecked({checked: enabled});
  return response;
}
try {
  admin = await session('journeyAdminQA', 'admin');
  if (await setGrant(true, 'Path A E2E: restore specialist access before the allowed run')) console.log('Restored grant left revoked by an earlier run');
  business = await session('journeyQA', 'business');
  const {page, read} = business;
  await page.getByRole('button', {name: 'Create agent', exact: true}).click();
  await page.getByRole('radio', {name: `Business templates Select ${TEMPLATE}`, exact: true}).check();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('textbox', {name: 'Agent name', exact: true}).fill(`Path A risk review ${Date.now()}`);
  await page.getByRole('button', {name: /^Model /}).click();
  await page.getByRole('option', {name: /Claude Haiku 4\.5/}).click();
  await page.screenshot({path: resolve(directory, 'configure.png'), fullPage: true});
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  const savedResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/journey/agents'));
  await page.getByRole('button', {name: 'Deploy to AgentCore', exact: true}).click();
  const saved = await (await savedResponse).json();
  if (!saved.agent_id) throw new Error('Create failed: ' + JSON.stringify(saved));
  evidence.agent_id = saved.agent_id;
  console.log('Created agent', saved.agent_id);
  const deployed = await waitStatus(read, saved.agent_id, 'deployment', 'DEPLOYED');
  if (!deployed.definition.tools.includes(TOOL_ID)) throw new Error('Deployed definition does not pin the specialist tool');
  evidence.deployment = {runtime_arn: deployed.deployment.binding?.arn, status: deployed.deployment.status, version: deployed.definition.version};
  await expect(page.getByText('Deployed', {exact: true})).toBeVisible();

  // Allowed call.
  const allowedResponse = await submitChat(page);
  if (!allowedResponse.ok()) throw new Error(`Allowed chat submission failed: HTTP ${allowedResponse.status()}`);
  const accepted = await allowedResponse.json();
  let job;
  await expect.poll(async () => {
    const r = await read('/api/journey/jobs/' + accepted.job_id);
    if (r.status() === 409) return 'RETRY';
    if (!r.ok()) throw new Error(`Chat status HTTP ${r.status()}`);
    job = await r.json();
    if (['FAILED', 'UNKNOWN', 'ERROR'].includes(job.phase)) throw new Error(job.error);
    return job.phase;
  }, {timeout: 300000, intervals: [5000, 10000], message: 'Runtime chat invocation succeeds'}).toBe('SUCCEEDED');
  await expect(page.getByLabel('Agent output')).toBeVisible();
  const calls = JSON.stringify(job.tool_calls || []);
  if (!calls.includes(GATEWAY_TOOL)) throw new Error('Allowed run did not call the specialist through the Gateway: ' + calls);
  evidence.allowed = {job_id: accepted.job_id, phase: job.phase, trace_id: job.trace_id, tool_calls: job.tool_calls,
    output: job.output, at: new Date().toISOString()};
  save();
  await page.screenshot({path: resolve(directory, 'allowed.png'), fullPage: true});
  console.log('Allowed governed specialist call: PASS', JSON.stringify({trace_id: job.trace_id, tool_calls: job.tool_calls}));

  // Admin revokes the business identity's grant in the Studio UI.
  const revokeResponse = await setGrant(false, 'Path A E2E: revoke specialist access for synthetic business QA user');
  evidence.revocation = {persona: state.journeyQA.subject, component: TOOL_ID, status: revokeResponse.status(), at: new Date().toISOString()};
  await admin.page.screenshot({path: resolve(directory, 'revoked.png'), fullPage: true});
  console.log('Admin grant revocation in Studio: PASS');

  // Denied call: same deployed Agent, same user, after revocation. The Studio
  // re-reads readiness and blocks Run; the backend independently refuses the
  // same invoke request the UI would send.
  await page.reload({waitUntil: 'domcontentloaded'});
  const alert = page.getByText('Review catalog capabilities', {exact: true});
  await expect(alert).toBeVisible();
  await page.getByRole('textbox', {name: 'Your question', exact: true}).fill(QUESTION);
  await expect(page.getByRole('button', {name: 'Run agent', exact: true})).toBeDisabled();
  const issues = (await (await read('/api/journey/agents/' + saved.agent_id)).json()).readiness.issues;
  // Sent from the Studio page exactly like its api() helper (same-origin + CSRF).
  const denied = await page.evaluate(async ({agentId, version, input}) => {
    const {csrf} = await (await fetch('/api/me', {credentials: 'same-origin'})).json();
    const r = await fetch(`/api/journey/agents/${agentId}/invoke`, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      body: JSON.stringify({version, idempotency_key: crypto.randomUUID(), input, conversation_id: null})});
    return {status: r.status, body: await r.text()};
  }, {agentId: saved.agent_id, version: deployed.current_version, input: QUESTION});
  evidence.denied = {ui: {run_agent: 'disabled', readiness_issues: issues}, status: denied.status, body: denied.body, at: new Date().toISOString()};
  save();
  if (denied.status !== 403 || !denied.body.includes('not approved for execution')) {
    throw new Error(`Expected governed denial, got HTTP ${denied.status}: ${denied.body}`);
  }
  await page.screenshot({path: resolve(directory, 'denied.png'), fullPage: true});
  console.log('Revoked grant denies the next run: PASS', denied.body);
  evidence.result = 'PASS';
  save();
} catch (error) {
  // Only application pages are captured, never the credential form.
  for (const [name, s] of [['business', business], ['admin', admin]]) {
    if (s && s.page.url().startsWith(origin)) {
      await s.page.screenshot({path: resolve(directory, `failure-${name}.png`), fullPage: true}).catch(() => {});
    }
  }
  evidence.error = String(error.message).split('\n')[0];
  save();
  throw new Error(evidence.error);
} finally {
  await browser.close();
}
