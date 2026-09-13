// Actual hosted UI + Cognito + Gateway + AgentCore. No intercepted responses.
// Credentials stay in process memory; no auth trace or storage-state is written.
import {chromium, expect} from '@playwright/test';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {setTimeout as pause} from 'node:timers/promises';

const root = resolve(import.meta.dirname, '../..');
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
const receipts = [];
async function waitStatus(agentId, field, expected) {
  const deadline = Date.now() + 900000;
  let previous;
  while (Date.now() < deadline) {
    const response = await page.request.get(state.app.outputs.ApplicationOrigin + '/api/journey/agents/' + agentId);
    if (!response.ok()) throw new Error(`Hosted status returned HTTP ${response.status()}`);
    const detail = await response.json();
    const status = detail[field].status;
    writeFileSync(resolve(directory, 'current-agent.json'), JSON.stringify(detail, null, 2), {mode: 0o600});
    if (status !== previous) console.log(`${field}: ${status}`);
    previous = status;
    if (status === expected) return detail;
    if (['FAILED', 'ERROR', 'UNKNOWN', 'STALE', 'FAILED_QUALITY'].includes(status)) {
      throw new Error(`${field}: ${status}: ${detail[field].error || 'Review the recorded cases'}`);
    }
    await pause(10000);
  }
  throw new Error(`${field} did not complete within the test deadline`);
}
try {
  await page.goto(state.app.outputs.ApplicationOrigin, {waitUntil: 'domcontentloaded'});
  await page.getByRole('button', {name: 'Sign in / Open Studio', exact: true}).click();
  await page.locator('input[name="username"]:visible').fill(credentials.username);
  await page.locator('input[name="password"]:visible').fill(credentials.password);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  delete credentials.password;
  await expect(page.getByRole('heading', {name: 'My agents', exact: true})).toBeVisible({timeout: 60000});
  const me = await (await page.request.get(state.app.outputs.ApplicationOrigin + '/api/me')).json();
  if (me.persona.id !== state.journeyQA.subject || me.persona.role !== 'business') throw new Error('Incorrect hosted business identity');
  console.log('Hosted Cognito business sign-in: PASS');
  const scenarios = process.env.GAB_LIVE_SCENARIO ? [JSON.parse(process.env.GAB_LIVE_SCENARIO)] : [
    {template: 'Research', eval: false}, {template: 'Knowledge Q&A', eval: false},
    {template: 'Research', eval: true, model: 'GPT-6 Astra'}, {template: 'Knowledge Q&A', eval: true},
  ];
  for (const [index, scenario] of scenarios.entries()) {
    if (index) await page.goto(state.app.outputs.ApplicationOrigin, {waitUntil: 'domcontentloaded'});
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
    await page.getByRole('textbox', {name: 'Your question', exact: true}).fill(question);
    await page.getByRole('button', {name: 'Run agent', exact: true}).click();
    await expect(page.getByLabel('Agent output')).toBeVisible({timeout: 300000});
    const detail = await (await page.request.get(state.app.outputs.ApplicationOrigin + '/api/journey/agents/' + saved.agent_id)).json();
    if (detail.last_invocation?.phase !== 'SUCCEEDED' || !detail.last_invocation.tool_calls?.length) throw new Error('Missing actual Gateway call evidence');
    if (scenario.eval && (detail.evaluation.cases.length !== 2 || detail.evaluation.cases.some(c => !c.request_id || c.evaluator_id !== 'Builtin.Correctness'))) throw new Error('Missing native evaluation receipts');
    if (!scenario.eval && (detail.evaluation.status !== 'SKIPPED' || detail.evaluation.cases.length)) throw new Error('Evaluation was not skipped');
    receipts.push({scenario, ...detail});
    writeFileSync(resolve(directory, 'receipts.json'), JSON.stringify(receipts, null, 2), {mode: 0o600});
    await page.screenshot({path: resolve(directory, `result-${index}.png`), fullPage: true});
    console.log('Hosted journey PASS:', JSON.stringify({scenario, agent_id: saved.agent_id,
      model: detail.last_invocation.model_id, tools: detail.last_invocation.tool_calls.map(c => c.name),
      evaluation: detail.evaluation.status}));
  }
} catch (error) {
  // Only the application page can be captured, never the credential form.
  if (page.url().startsWith(state.app.outputs.ApplicationOrigin)) {
    await page.screenshot({path: resolve(directory, 'failure.png'), fullPage: true}).catch(() => {});
    writeFileSync(resolve(directory, 'failure-page.txt'), await page.locator('body').innerText(), {mode: 0o600});
  }
  throw error;
} finally {
  await browser.close();
}
