// Real hosted admin/business handoff. No mocked API responses or saved sessions.
import {chromium, expect as baseExpect} from '@playwright/test';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {setTimeout as pause} from 'node:timers/promises';

const root = resolve(import.meta.dirname, '../..');
const statePath = process.env.GAB_RELEASE_STATE;
if (!statePath) throw new Error('Explicit GAB_RELEASE_STATE required');
const state = JSON.parse(readFileSync(statePath, 'utf8'));
if (state.target.account !== '820242898417' || state.target.profile !== 'account-820') throw new Error('Unexpected account');
const origin = state.app.outputs.ApplicationOrigin;
const directory = resolve(root, 'artifacts/admin-live');
mkdirSync(directory, {recursive: true});
const expect = baseExpect.configure({timeout: 45000});
const browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-quic']});
const pages = [];
async function login(role) {
  const identity = role === 'admin' ? 'journeyAdminQA' : 'journeyQA';
  const loaded = spawnSync(resolve(root, '.venv/bin/python'), ['-c', `
import boto3,json,sys
state=json.load(open(sys.argv[1]));s=boto3.Session(profile_name='account-820',region_name='us-west-2')
assert s.client('sts').get_caller_identity()['Account']=='820242898417'
ssm=s.client('ssm');prefix=state[sys.argv[2]]['parameterPrefix']
print(json.dumps({key:ssm.get_parameter(Name=prefix+'/'+key,WithDecryption=True)['Parameter']['Value'] for key in ('username','password')}))
`, statePath, identity], {encoding: 'utf8', cwd: root});
  if (loaded.status !== 0) throw new Error('Scoped QA credentials unavailable');
  const credentials = JSON.parse(loaded.stdout);
  const context = await browser.newContext({viewport: {width: 1440, height: 1080}});
  const page = await context.newPage(); pages.push(page);
  await page.goto(origin, {waitUntil: 'domcontentloaded'});
  await page.getByRole('button', {name: 'Sign in / Open Studio', exact: true}).click();
  const username = page.locator('input[name="username"]:visible'), password = page.locator('input[name="password"]:visible');
  await username.waitFor();
  await page.waitForLoadState('networkidle', {timeout: 15000}).catch(() => {});
  await expect.poll(async () => {
    await username.fill(credentials.username); await password.fill(credentials.password); await password.press('Tab'); await pause(250);
    return await username.inputValue() === credentials.username && await password.inputValue() === credentials.password;
  }).toBe(true);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  delete credentials.password;
  await expect(page.getByRole('heading', {name: role === 'admin' ? 'Platform overview' : 'My agents', exact: true})).toBeVisible();
  const response = await page.request.get(origin + '/api/me');
  const me = await response.json();
  if (me.persona.id !== state[identity].subject || me.persona.role !== role) throw new Error('Incorrect hosted role');
  return page;
}
try {
  const user = await login('business'), admin = await login('admin');
  console.log('Actual Cognito business and administrator sign-in: PASS');
  const denied = await user.request.get(origin + '/api/admin/platform/overview');
  if (denied.status() !== 403) throw new Error('Business role reached administrator metrics');
  await user.getByRole('link', {name: 'Tool requests', exact: true}).click();
  const details = `Synthetic administrator handoff ${Date.now()}`;
  await user.getByRole('textbox', {name: 'What tool do you need?', exact: true}).fill('CRM');
  await user.getByRole('textbox', {name: 'Details', exact: true}).fill(details);
  const submitted = user.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/tool-requests'));
  await user.getByRole('button', {name: 'Send request', exact: true}).click();
  const response = await submitted;
  if (response.status() !== 201) throw new Error(`Request creation HTTP ${response.status()}`);
  const request = await response.json();
  await admin.getByRole('link', {name: 'Policies & approvals', exact: true}).click();
  const row = admin.getByRole('row').filter({hasText: details});
  await row.getByRole('textbox', {name: 'Response for CRM', exact: true}).fill('Reviewing the synthetic CRM tool request.');
  await row.getByRole('button', {name: 'Save response', exact: true}).click();
  await expect(admin.getByText('Response saved.', {exact: true})).toBeVisible();
  await expect(user.getByRole('row').filter({hasText: details}).getByText('In review', {exact: true})).toBeVisible();
  await admin.screenshot({path: resolve(directory, 'approvals.png'), fullPage: true});
  await user.screenshot({path: resolve(directory, 'business-status.png'), fullPage: true});
  const readback = spawnSync(resolve(root, '.venv/bin/python'), ['-c', `
import boto3,json,sys
from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get
state=json.load(open(sys.argv[1]));s=boto3.Session(profile_name='account-820',region_name='us-west-2')
assert s.client('sts').get_caller_identity()['Account']=='820242898417'
store=DynamoStore(state['app']['outputs']['StateTable'],s.resource('dynamodb'))
with store.tx() as db:
 row=get(db,'tool-request:'+sys.argv[2])
 assert row['requester']==state['journeyQA']['subject'] and row['responded_by']==state['journeyAdminQA']['subject']
 assert row['status']=='IN_REVIEW' and row['version']==2
 assert any(r['resource']==row['id'] and r['action']=='tool_request_responded' for r in db.select('audit'))
 print(json.dumps({k:row[k] for k in ('id','title','status','version','response')}))
`, statePath, request.id], {encoding: 'utf8', cwd: root, timeout: 60000});
  if (readback.status !== 0) throw new Error('Independent DynamoDB/audit readback failed');
  writeFileSync(resolve(directory, 'request-ddb-receipt.json'), readback.stdout, {mode: 0o600});
  console.log('Business request → admin response → automatic user status → DDB + audit: PASS');
  const sources = {};
  for (const [navigation, endpoint, name] of [
    ['Platform overview', '/overview', 'overview'],
    ['Performance', '/performance/models', 'performance'],
    ['Platform cost', '/costs', 'costs'],
  ]) {
    await admin.getByRole('link', {name: navigation, exact: true}).click();
    const response = await admin.request.get(origin + '/api/admin/platform' + endpoint, {timeout: 45000});
    if (!response.ok()) throw new Error(`Native ${name} returned HTTP ${response.status()}`);
    sources[name] = await response.json();
    await expect(admin.getByRole('button', {name: 'Refresh metrics', exact: true})).toBeEnabled();
    await admin.screenshot({path: resolve(directory, name + '.png'), fullPage: true});
    console.log('Hosted native administrator source: PASS', name);
  }
  await admin.getByRole('link', {name: 'Registry & AI Catalog', exact: true}).click();
  await admin.getByRole('button', {name: 'Discover Bedrock models'}).click();
  await expect(admin.getByText('Bedrock model discovery refreshed.', {exact: true})).toBeVisible();
  const discovered = await admin.request.get(origin + '/api/admin/platform/models/discovery');
  if (!discovered.ok()) throw new Error('Native model discovery unavailable');
  sources.discovered_models = (await discovered.json()).length;
  const registry = await admin.request.get(origin + '/api/admin/platform/registry');
  sources.registry = {http_status: registry.status(), result: await registry.json()};
  await admin.screenshot({path: resolve(directory, 'registry.png'), fullPage: true});
  writeFileSync(resolve(directory, 'native-sources.json'), JSON.stringify(sources, null, 2), {mode: 0o600});
  console.log('Actual Bedrock model discovery: PASS; Registry status:', sources.registry.http_status);
} catch (error) {
  // Raw Playwright transport errors can include cookie headers.
  console.error('Hosted admin test failed:', String(error.message).split('\n')[0]);
  for (const [index, page] of pages.entries()) {
    if (page.url().startsWith(origin)) await page.screenshot({path: resolve(directory, `failure-${index}.png`), fullPage: true}).catch(() => {});
  }
  process.exitCode = 1;
} finally {
  await browser.close();
}
