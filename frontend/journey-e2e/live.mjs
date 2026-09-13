// Actual hosted UI + Cognito + Gateway + AgentCore. No intercepted responses.
// Credentials stay in process memory; no auth trace or storage-state is written.
import {chromium, expect as baseExpect} from '@playwright/test';
import {readFileSync, mkdirSync, writeFileSync, existsSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {setTimeout as pause} from 'node:timers/promises';

const root = resolve(import.meta.dirname, '../..');
const expect = baseExpect.configure({timeout: 45000});
const statePath = process.env.GAB_RELEASE_STATE;
if (!statePath) throw new Error('Explicit GAB_RELEASE_STATE is required');
const state = JSON.parse(readFileSync(statePath, 'utf8'));
if (state.target.account !== '820242898417' || state.target.profile !== 'account-820') throw new Error('Unexpected test target');
const secret = spawnSync(resolve(root, '.venv/bin/python'), ['-c', `
import boto3,json,sys
s=json.load(open(sys.argv[1]))
session=boto3.Session(profile_name=s['target']['profile'],region_name=s['target']['region'])
assert session.client('sts').get_caller_identity()['Account']==s['target']['account']
ssm=session.client('ssm')
print(json.dumps({k:ssm.get_parameter(Name=s['journeyQA']['parameterPrefix']+'/'+k,WithDecryption=True)['Parameter']['Value'] for k in ('username','password')}))
`, statePath], {encoding: 'utf8', cwd: root});
if (secret.status !== 0) throw new Error('Could not load the scoped QA credentials');
const credentials = JSON.parse(secret.stdout);
const directory = resolve(root, 'artifacts/journey-live');
mkdirSync(directory, {recursive: true});
const browser = await chromium.launch({channel: 'chrome', headless: true, args: ['--disable-quic']});
const context = await browser.newContext({viewport: {width: 1440, height: 1080}});
const page = await context.newPage();
page.setDefaultTimeout(30000);
const receiptsPath = resolve(directory, 'receipts.json');
const receipts = existsSync(receiptsPath) ? JSON.parse(readFileSync(receiptsPath, 'utf8')) : [];
async function read(path) {
  try {
    return await page.request.get(state.app.outputs.ApplicationOrigin + path, {maxRetries: 3, timeout: 45000});
  } catch {
    // Playwright's raw transport errors include cookie headers.
    throw new Error('Hosted status request failed after transport retries');
  }
}
async function waitStatus(agentId, field, expected) {
  const deadline = Date.now() + 900000;
  let previous;
  while (Date.now() < deadline) {
    const response = await read('/api/journey/agents/' + agentId);
    if (response.status() === 409 && (await response.json()).detail === 'Concurrent governance update; reload and retry') {
      await pause(1000);
      continue;
    }
    if (!response.ok()) throw new Error(`Hosted status returned HTTP ${response.status()}`);
    const detail = await response.json();
    const status = detail[field]?.status || 'NOT_REQUESTED';
    writeFileSync(resolve(directory, 'current-agent.json'), JSON.stringify(detail, null, 2), {mode: 0o600});
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
async function runChat(question) {
  await page.getByRole('textbox', {name: 'Your question', exact: true}).fill(question);
  const submitted = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/invoke'));
  await page.getByRole('button', {name: 'Run agent', exact: true}).click();
  const response = await submitted;
  if (!response.ok()) throw new Error(`Chat submission failed: HTTP ${response.status()}`);
  const accepted = await response.json();
  await expect.poll(async () => {
    const r = await read('/api/journey/jobs/' + accepted.job_id);
    if (r.status() === 409) return 'RETRY';
    if (!r.ok()) throw new Error(`Chat status HTTP ${r.status()}`);
    const result = await r.json();
    if (['FAILED', 'UNKNOWN', 'ERROR'].includes(result.phase)) throw new Error(result.error);
    return result.phase;
  }, {timeout: 300000, intervals: [5000, 10000], message: 'Actual Runtime chat invocation succeeds'}).toBe('SUCCEEDED');
  await expect(page.getByLabel('Agent output')).toBeVisible();
  console.log('Actual Runtime chat turn: PASS');
}

function nativeApi(detail) {
  const result = spawnSync(resolve(root, '.venv/bin/python'), ['-c', `
import boto3,json,sys,uuid
from botocore.config import Config
d=json.loads(sys.argv[1])
s=boto3.Session(profile_name='account-820',region_name='us-west-2')
assert s.client('sts').get_caller_identity()['Account']=='820242898417'
request_id=uuid.uuid4().hex
r=s.client('bedrock-agentcore',config=Config(read_timeout=210,retries={'total_max_attempts':1})).invoke_agent_runtime(
 agentRuntimeArn=d['deployment']['binding']['arn'],qualifier='DEFAULT',runtimeSessionId='gab-'+request_id,
 contentType='application/json',payload=json.dumps({'input':'Use web search to explain what AgentCore Gateway does. Cite AWS documentation.','request_id':request_id}).encode())
v=json.loads(r['response'].read())
assert v['status']=='SUCCEEDED' and v['definition_digest']==d['definition']['digest'] and v['tool_calls']
print(json.dumps({k:v[k] for k in ('status','output','model_id','tool_calls','trace_id','session_id')}))
`, JSON.stringify(detail)], {encoding: 'utf8', cwd: root, timeout: 240000});
  if (result.status !== 0) throw new Error('Native API verification failed: ' + result.stderr);
  const receipt = JSON.parse(result.stdout);
  writeFileSync(resolve(directory, 'native-api-receipt.json'), JSON.stringify(receipt, null, 2), {mode: 0o600});
  console.log('Native Runtime API + Gateway tool call: PASS');
}

async function removeAgent(detail, index) {
  if (!detail.deletion || detail.deletion.status === 'DELETE_FAILED') {
    await page.getByRole('button', {name: 'Delete agent', exact: true}).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('button', {name: 'Permanently delete', exact: true})).toBeDisabled();
    await dialog.getByRole('textbox', {name: 'Agent name to confirm deletion'}).fill('incorrect confirmation');
    await expect(dialog.getByRole('button', {name: 'Permanently delete', exact: true})).toBeDisabled();
    await dialog.getByRole('button', {name: 'Cancel', exact: true}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await page.getByRole('button', {name: 'Delete agent', exact: true}).click();
    await dialog.getByRole('textbox', {name: 'Agent name to confirm deletion'}).fill(detail.definition.name);
    await page.screenshot({path: resolve(directory, `delete-confirm-${index}.png`), fullPage: true});
    const submitted = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/delete'));
    await dialog.getByRole('button', {name: 'Permanently delete', exact: true}).click();
    const response = await submitted;
    if (!response.ok()) throw new Error(`Deletion submission failed: HTTP ${response.status()}`);
  }
  const removed = await waitStatus(detail.id, 'deletion', 'DELETED');
  await expect(page.getByText('Agent deleted', {exact: true}), 'Deletion completion appears in the current view').toBeVisible();
  console.log('Deletion page before reload:', new URL(page.url()).hash);
  await page.reload({waitUntil: 'domcontentloaded'});
  console.log('Deletion page after reload:', new URL(page.url()).hash);
  await expect(page.getByText('Agent deleted', {exact: true}), 'Deletion receipt survives refresh').toBeVisible();
  const verified = spawnSync(resolve(root, '.venv/bin/python'), ['-c', `
import boto3,json,sys
from botocore.exceptions import ClientError
from foundation_harness.config import digest
d=json.loads(sys.argv[1]);s=boto3.Session(profile_name='account-820',region_name='us-west-2')
assert s.client('sts').get_caller_identity()['Account']=='820242898417'
control=s.client('bedrock-agentcore-control')
name='gab_journey_'+digest([d['definition']['digest'],'deploy'])[:24]
for operation,field in (('list_agent_runtimes','agentRuntimes'),('list_workload_identities','workloadIdentities')):
 token=None
 while True:
  r=getattr(control,operation)(**({'nextToken':token} if token else {}))
  assert not any(item.get('agentRuntimeName',item.get('name','')).split('-')[0]==name for item in r.get(field,[])), 'Agent Runtime or identity remains'
  token=r.get('nextToken')
  if not token:break
state=json.load(open(sys.argv[2]))
assert control.get_gateway(gatewayIdentifier=state['journeyPlatform']['gateway_id'])['status']=='READY'
b=d['deployment'].get('binding')
if b:
 try:s.client('bedrock-agentcore-control').get_agent_runtime(agentRuntimeId=b['id'])
 except ClientError as e:assert e.response['Error']['Code']=='ResourceNotFoundException'
 else:raise AssertionError('Runtime still exists')
 s3=s.client('s3');bucket=b['manifest']['bucket']
 for prefix in (b['manifest']['key'],'journey/evidence/'+d['definition']['digest']+'/'):
  r=s3.list_object_versions(Bucket=bucket,Prefix=prefix)
  assert not r.get('Versions') and not r.get('DeleteMarkers'), 'Agent objects remain'
print('Native resources removed')
`, JSON.stringify(detail), statePath], {encoding: 'utf8', cwd: root, timeout: 60000});
  if (verified.status !== 0) throw new Error('Cleanup verification failed: ' + verified.stderr);
  writeFileSync(resolve(directory, `deleted-${index}.json`), JSON.stringify(removed, null, 2), {mode: 0o600});
  console.log('Confirmed deletion + native resource cleanup: PASS', detail.id);
}
try {
  await page.goto(state.app.outputs.ApplicationOrigin, {waitUntil: 'domcontentloaded'});
  await page.getByRole('button', {name: 'Sign in / Open Studio', exact: true}).click();
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
    const state = await Promise.race([
      page.getByRole('heading', {name: 'My agents', exact: true}).waitFor({timeout: 60000}).then(() => 'signed-in'),
      page.getByText('Missing email address.', {exact: true}).waitFor({timeout: 60000}).then(() => 'empty-form'),
    ]).catch(() => 'failed');
    if (state === 'signed-in') break;
    // Cognito can hydrate its server-rendered form after an initial fast fill.
    // Retry only its local empty-form validation, never rejected credentials.
    if (state !== 'empty-form' || attempt === 1) throw new Error('Hosted Cognito sign-in did not complete');
  }
  delete credentials.password;
  const me = await (await read('/api/me')).json();
  if (me.persona.id !== state.journeyQA.subject || me.persona.role !== 'business') throw new Error('Incorrect hosted business identity');
  console.log('Hosted Cognito business sign-in: PASS');
  await page.getByRole('link', {name: 'Tool requests', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Request a new tool', exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Your capability request history', exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Send request', exact: true})).toBeDisabled();
  const toolTitle = `Synthetic QA tool request ${Date.now()}`;
  await page.getByRole('textbox', {name: 'What tool do you need?', exact: true}).fill(toolTitle);
  await page.getByRole('textbox', {name: 'Details', exact: true}).fill('Synthetic end-to-end test: request a new tool that is not in the Catalog.');
  await page.getByRole('button', {name: 'Send request', exact: true}).click();
  await expect(page.getByText('Tool request sent.', {exact: true})).toBeVisible();
  await expect(page.getByRole('cell', {name: toolTitle, exact: true})).toBeVisible();
  await page.screenshot({path: resolve(directory, 'tool-requests.png'), fullPage: true});
  console.log('Hosted new tool request submission + own status: PASS');
  for (const id of JSON.parse(process.env.GAB_CLEANUP_IDS || '[]')) {
    const detail = await (await read('/api/journey/agents/' + id)).json();
    await page.goto(state.app.outputs.ApplicationOrigin + '/#agent/' + id, {waitUntil: 'domcontentloaded'});
    await page.reload({waitUntil: 'domcontentloaded'});
    console.log('Opened agent page:', new URL(page.url()).hash);
    if (detail.deletion?.status === 'DELETED') {
      await expect(page.getByText('Agent deleted', {exact: true}), 'Existing deletion receipt can be reopened').toBeVisible();
      continue;
    }
    await expect(page.getByRole('heading', {name: detail.definition.name, exact: true})).toBeVisible();
    await removeAgent(detail, 'failed-' + id);
  }
  const scenarios = process.env.GAB_CLEANUP_IDS ? [] : process.env.GAB_LIVE_SCENARIO ? [JSON.parse(process.env.GAB_LIVE_SCENARIO)] : [
    {template: 'Research', eval: true, model: 'GPT-6 Astra'}, {template: 'Knowledge Q&A', eval: true},
    {template: 'Research', eval: false}, {template: 'Knowledge Q&A', eval: false},
  ];
  for (const [index, scenario] of scenarios.entries()) {
    await page.goto(state.app.outputs.ApplicationOrigin, {waitUntil: 'domcontentloaded'});
    await page.reload({waitUntil: 'domcontentloaded'});
    await expect(page.getByRole('heading', {name: 'My agents', exact: true})).toBeVisible();
    await page.getByRole('button', {name: 'Create agent', exact: true}).click();
    await expect(page.getByRole('radio')).toHaveCount(2);
    await page.getByRole('radio', {name: `Business templates Select ${scenario.template}`, exact: true}).check();
    await page.getByRole('button', {name: 'Next', exact: true}).click();
    await page.getByRole('textbox', {name: 'Agent name', exact: true}).fill(`Journey QA ${scenario.template} ${scenario.eval ? 'eval' : 'skip'} ${Date.now()}`);
    if (scenario.model) {
      await page.getByRole('button', {name: /^Model /}).click();
      await page.getByRole('option', {name: new RegExp(scenario.model)}).click();
    }
    await expect(page.getByText('MCP servers', {exact: true})).toBeVisible();
    await expect(page.getByRole('checkbox')).toHaveCount(0);
    await page.screenshot({path: resolve(directory, `configure-${index}.png`), fullPage: true});
    await page.getByRole('button', {name: 'Next', exact: true}).click();
    if (scenario.eval) await page.getByRole('button', {name: 'Use synthetic sample'}).click();
    await page.getByRole('button', {name: 'Next', exact: true}).click();
    const savedResponse = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/api/journey/agents'));
    await page.getByRole('button', {name: 'Deploy to AgentCore', exact: true}).click();
    const saved = await (await savedResponse).json();
    if (!saved.agent_id) throw new Error('Create failed: ' + JSON.stringify(saved));
    console.log('Created:', JSON.stringify({scenario, ...saved}));
    // Poll the authenticated UI until it displays the native deployment result.
    await waitStatus(saved.agent_id, 'deployment', 'DEPLOYED');
    await expect(page.getByText('Deployed', {exact: true})).toBeVisible();
    await waitStatus(saved.agent_id, 'evaluation', scenario.eval ? 'PASSED' : 'SKIPPED');
    await expect(page.getByText(scenario.eval ? 'Evaluation passed' : 'Skipped — no dataset', {exact: true})).toBeVisible();
    await page.reload({waitUntil: 'domcontentloaded'});
    await expect(page.getByText('Deployed', {exact: true})).toBeVisible();
    const question = scenario.template === 'Research'
      ? 'Use web search to explain the difference between Amazon Bedrock AgentCore Runtime and Gateway. Cite AWS documentation.'
      : 'What is the Aurora support response target? Search the knowledge documents and cite the source.';
    await runChat(question);
    await runChat('Summarize your previous answer in one sentence, retaining its source references.');
    await expect(page.getByRole('heading', {name: 'You', exact: true})).toHaveCount(2);
    await page.reload({waitUntil: 'domcontentloaded'});
    await expect(page.getByRole('heading', {name: 'You', exact: true})).toHaveCount(2);
    const detail = await (await read('/api/journey/agents/' + saved.agent_id)).json();
    const conversationTools = detail.conversation?.messages.flatMap(message => message.tools || []) || [];
    if (detail.last_invocation?.phase !== 'SUCCEEDED' || !conversationTools.length) throw new Error('Missing actual Gateway call evidence');
    if (scenario.eval && (detail.evaluation.cases.length !== 2 || detail.evaluation.cases.some(c => !c.request_id || c.evaluator_id !== 'Builtin.Correctness'))) throw new Error('Missing native evaluation receipts');
    if (!scenario.eval && (detail.evaluation.status !== 'SKIPPED' || detail.evaluation.cases.length)) throw new Error('Evaluation was not skipped');
    receipts.push({scenario, ...detail});
    writeFileSync(receiptsPath, JSON.stringify(receipts, null, 2), {mode: 0o600});
    await page.screenshot({path: resolve(directory, `result-${index}.png`), fullPage: true});
    await page.getByRole('tab', {name: 'API access', exact: true}).click();
    await expect(page.getByRole('textbox', {name: 'Python API example'})).toHaveValue(/invoke_agent_runtime/);
    if (index === 0 && scenario.template === 'Research') nativeApi(detail);
    console.log('Hosted journey PASS:', JSON.stringify({scenario, agent_id: saved.agent_id,
      model: detail.last_invocation.model_id, tools: conversationTools,
      evaluation: detail.evaluation.status}));
    await removeAgent(detail, index);
  }
} catch (error) {
  // Only the application page can be captured, never the credential form.
  if (page.url().startsWith(state.app.outputs.ApplicationOrigin)) {
    await page.screenshot({path: resolve(directory, 'failure.png'), fullPage: true}).catch(() => {});
    writeFileSync(resolve(directory, 'failure-page.txt'), await page.locator('body').innerText(), {mode: 0o600});
  }
  throw new Error(String(error.message).split('\n')[0]);
} finally {
  await browser.close();
}
