// Offline browser test: synthetic API responses, production-built UI, no external requests.
import {chromium} from '@playwright/test';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const four=process.argv.includes('--four-fields');
const root=path.resolve('frontend/dist');
const value=JSON.parse(fs.readFileSync(four?'work/ui-four-field-fixtures.json':'work/result-evidence-browser-fixture.json','utf8'));
const server=http.createServer((req,res)=>{
 const file=path.join(root,req.url==='/'?'index.html':req.url);
 if(!file.startsWith(root+'/')){res.writeHead(403).end();return;}
 try {res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(file));}
 catch {res.writeHead(404).end();}
});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
let browser;
try {
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100},serviceWorkers:'block'});
 page.setDefaultTimeout(8000);
 const errors=[],external=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 const base=`http://127.0.0.1:${server.address().port}`;
 const job={id:'job-synthetic',version:1,stage:'BLOCKED',stale:false,events:[],result:{mode:'live',gate:'EVIDENCE_INCOMPLETE',result_evidence:value}};
 const jobs=four?value:[job];
 const version=four?4:1;
 const definition={name:'Synthetic evidence browser test',foundation_id:'research',foundation_version:'1',digest:four?'4'.repeat(64):'d'.repeat(64)};
 const agent={id:'agent-synthetic',name:definition.name,current_version:version,foundation_id:'research',job:jobs[0]};
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.origin!==base){external.push(url.origin);return route.abort();}
  requests.push(url.pathname);
  let data;
  if(url.pathname==='/studio-config.json')data={hosted:false,mode:'LOCAL SIMULATION'};
  else if(url.pathname==='/api/demo/personas')data={personas:[]};
  else if(url.pathname==='/api/me')data={csrf:'synthetic',persona:{id:'alex',role:'business',workspace:'research',workspace_name:'Synthetic test',name:'Synthetic test'}};
  else if(url.pathname==='/api/agents')data=[agent];
  else if(url.pathname==='/api/agents/agent-synthetic')data={...agent,definition,versions:jobs.map(j=>({version:j.version,digest:j.result.result_evidence.definition_digest})),jobs};
  else if(url.pathname.startsWith('/api/jobs/'))data=jobs.find(j=>url.pathname==='/api/jobs/'+j.id);
  else if(url.pathname.startsWith('/api/'))return route.fulfill({status:404,body:'{}'});
  if(data)return route.fulfill({json:data});
  return route.continue();
 });
 await page.goto(base);
 await page.getByRole('link',{name:definition.name,exact:true}).click();
 if(four){
  const results=[];
  for(const j of jobs){
   const p=j.result.result_evidence;
   const result={scenario:j.name,version:j.version,quality:p.quality_status,gatePassed:j.result.passed,report:'FAIL',evaluationIDs:'FAIL',traceIDs:'FAIL',pricing:'FAIL'};
   try{
    await page.getByRole('button',{name:'Immutable result replay'}).click();
    await page.getByRole('option',{name:`v${j.version} · ${j.id} · ${j.stage}`,exact:true}).click();
    await page.getByText(`Immutable definition v${j.version}.`,{exact:false}).waitFor();
    assert.equal(await page.getByRole('link',{name:/Authenticated recorded result/}).getAttribute('href'),'/api/jobs/'+j.id);
    await page.getByRole('tab',{name:'Report',exact:true}).click();
    const report=page.getByLabel('Verified report');
    assert((await report.innerText()).includes(p.report.text));
    assert.equal(await report.locator('img,script').count(),0);
    result.report='PASS';
    assert((await page.locator('body').innerText()).includes('Quality: '+p.quality_status));
    await page.screenshot({path:`work/ui-four-field-${j.name}-report.png`,fullPage:true});
    await page.getByRole('tab',{name:'Evaluation',exact:true}).click();
    for(const e of p.evaluations)await page.getByText(e.id,{exact:true}).waitFor();
    result.evaluationIDs='PASS';
    if(j.name==='bound-fail'){assert.equal(j.result.passed,false);await page.getByRole('cell',{name:'FAIL',exact:true}).waitFor();}
    if(j.name==='deterministic-incomplete'){assert.equal(j.result.passed,false);await page.getByRole('cell',{name:'Missing / BLOCKED',exact:true}).waitFor();assert(!/Live pass|LIVE_PASS/.test(await page.locator('body').innerText()));}
    fs.writeFileSync(`work/ui-four-field-${j.name}-evaluation.txt`,await page.locator('body').innerText());
    await page.getByRole('tab',{name:'Execution',exact:true}).click();
    assert((await page.locator('body').innerText()).includes('Trace IDs: '+p.trace_ids.join(', ')));
    result.traceIDs='PASS';
    await page.getByRole('tab',{name:'Cost',exact:true}).click();
    const body=await page.locator('body').innerText();
    if(p.cost.status==='ESTIMATED'){assert(Number(p.cost.estimated_model_inference_cost)>0);assert(body.includes(`${p.cost.currency} ${p.cost.estimated_model_inference_cost}`));}
    else{assert(body.includes('UNKNOWN'));assert(!body.includes('USD 0'));}
    result.pricing='PASS';
    await page.getByText(/Unallocated charges:/).waitFor();
    fs.writeFileSync(`work/ui-four-field-${j.name}-cost.txt`,body);
    await page.screenshot({path:`work/ui-four-field-${j.name}-cost.png`,fullPage:true});
   }catch(e){result.error=e.message;await page.screenshot({path:`work/ui-four-field-${j.name}-failure.png`,fullPage:true});}
   results.push(result);console.log(JSON.stringify(result));
  }
  const summary={results,pageErrors:errors,externalAborted:external,requests};
  fs.writeFileSync('work/ui-four-field-browser-results.json',JSON.stringify(summary,null,2));
  assert.equal(errors.length,0);assert(results.every(r=>['report','evaluationIDs','traceIDs','pricing'].every(k=>r[k]==='PASS')));
  console.log('PASS: four current backend projections rendered through nested result.result_evidence; immutable replay selector and authenticated links; no JavaScript errors. Offline synthetic evidence, not live acceptance.');
 }else{
  await page.getByRole('tab',{name:'Report',exact:true}).click();
  await page.getByLabel('Verified report').waitFor();
  assert((await page.getByLabel('Verified report').innerText()).includes('<img src=x onerror=alert(1)>'));
  assert.equal(await page.getByLabel('Verified report').locator('img,script').count(),0);
  await page.getByRole('tab',{name:'Evaluation',exact:true}).click();
  await page.getByText('eval-synthetic',{exact:true}).waitFor();
  await page.getByRole('tab',{name:'Cost',exact:true}).click();
  await page.getByText('USD 0.00014',{exact:true}).waitFor();
  await page.getByText(/Unallocated charges:/).waitFor();
  assert.equal(await page.getByRole('link',{name:/Authenticated recorded result/}).getAttribute('href'),'/api/jobs/job-synthetic');
  await page.screenshot({path:'work/result-evidence-browser.png',fullPage:true});
  console.log('PASS: built Cloudscape Report/Evaluation/Cost; escaped HTML; authenticated replay link; unallocated charges. Synthetic mocked API only.');
 }
} finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
