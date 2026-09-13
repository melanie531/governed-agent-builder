import {test,expect,Page} from '@playwright/test';
import fs from 'node:fs';

async function selectOption(page:Page,label:string,option:string){
 await page.getByRole('button',{name:new RegExp(label)}).click();
 await page.getByRole('option').filter({hasText:option}).first().click();
}
async function switchPersona(page:Page,name:string){
 await page.getByRole('button',{name:/· (Research studio|Operations desk|Platform governance)/}).click();
 await page.getByRole('menuitem',{name:new RegExp(name)}).click();
 await expect(page.getByRole('button',{name:new RegExp(name+' · ')})).toBeVisible();
}
async function nav(page:Page,name:string){const link=page.getByRole('link',{name,exact:true});const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();await link.click();}
async function next(page:Page){await page.getByRole('button',{name:'Next',exact:true}).click();}

test('Cloudscape business journey: create, measured evidence, revise, retest, export and approval',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Governed Agent Builder',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Enter as Alex Morgan'}).click();
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-my-agents.png',fullPage:true});
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('radio',{name:'Select Research brief'}).click();
 await next(page);
 await selectOption(page,'Model route','Claude · Bedrock');
 await page.getByRole('button',{name:/^Tools \/ MCP/}).click();
 await page.getByRole('option').filter({hasText:'Synthetic knowledge search'}).click();
 await page.keyboard.press('Escape');
 await page.getByRole('button',{name:/^Skills/}).click();
 await page.getByRole('option').filter({hasText:'Evidence citations'}).click();
 await page.keyboard.press('Escape');
 await next(page);
 await page.getByRole('textbox',{name:/^Agent name/}).fill('Aurora research companion');
 await page.getByRole('textbox',{name:/^Your instructions/}).fill('Use only synthetic evidence. Cite sources. Refuse unknown questions. Uppercase.');
 await page.getByRole('textbox',{name:/^Success criteria/}).fill('Cite synthetic evidence and refuse unknown data.');
 await expect(page.getByRole('textbox',{name:/^Evaluation dataset/})).toHaveValue(/launch/);
 const uploaded=JSON.parse(await page.getByRole('textbox',{name:/^Evaluation dataset/}).inputValue());
 uploaded[0].id='uploaded-launch';
 await page.locator('input[type=file]').setInputFiles({name:'synthetic-cases.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(uploaded))});
 await expect(page.getByRole('textbox',{name:/^Evaluation dataset/})).toHaveValue(/uploaded-launch/);
 await page.screenshot({path:'../artifacts/cloudscape-wizard-evaluation.png',fullPage:true});
 await next(page);
 await expect(page.getByText('Aurora research companion',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Create & test agent',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Aurora research companion',exact:true})).toBeVisible();
 await expect(page.getByText('Sample test passed',{exact:true}).first()).toBeVisible();
 await expect(page.getByText('100 / 100 measured.',{exact:false})).toBeVisible();
 await expect(page.locator('pre').filter({hasText:'AURORA LAUNCHES IN OCTOBER'})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-results-pass.png',fullPage:true});
 await page.getByRole('tab',{name:'Run sample',exact:true}).click();
 await page.getByRole('button',{name:'Run current version'}).click();
 await expect(page.getByLabel('Sample output')).toContainText('OCTOBER');
 await page.getByRole('tab',{name:'Execution trace'}).click();
 await expect(page.getByText('Evaluation case recorded',{exact:false}).first()).toBeVisible();
 // Export from authorized backend, then inspect signature and filename.
 const downloadPromise=page.waitForEvent('download');
 await page.getByRole('button',{name:'Download sample source',exact:true}).first().click();
 const download=await downloadPromise;
 await download.saveAs('../artifacts/browser-export-v1.zip');
 expect(download.suggestedFilename()).toMatch(/-v1\.zip$/);
 expect(fs.readFileSync('../artifacts/browser-export-v1.zip').subarray(0,2).toString()).toBe('PK');
 // Create a genuine failed revision by changing expected evidence.
 await page.getByRole('button',{name:'Revise agent',exact:true}).click();
 await next(page);await next(page);
 await page.getByRole('button',{name:'Try a failing case'}).click();
 await next(page);await page.getByRole('button',{name:'Create & test agent'}).click();
 await expect(page.getByText('Changes needed',{exact:true}).first()).toBeVisible();
 await page.getByRole('tab',{name:'Evaluation results'}).click();
 await expect(page.getByText('Required term: impossible-fixture-term',{exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'Run sample',exact:true}).click();
 await expect(page.getByRole('button',{name:'Run current version'})).toBeDisabled();
 // Repair via editable JSON and demonstrate missing required judge gate.
 await page.getByRole('button',{name:'Revise agent',exact:true}).click();await next(page);await next(page);
 const dataset=JSON.parse(await page.getByRole('textbox',{name:/^Evaluation dataset/}).inputValue());
 dataset[0].required_terms=['Aurora','October'];
 await page.getByRole('textbox',{name:/^Evaluation dataset/}).fill(JSON.stringify(dataset,null,2));
 await page.getByRole('button',{name:'Try a missing judge'}).click();
 await next(page);await page.getByRole('button',{name:'Create & test agent'}).click();
 await expect(page.getByText('Required judge evidence is missing.',{exact:false})).toBeVisible();
 await expect(page.getByText('Changes needed',{exact:true}).first()).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-missing-judge.png',fullPage:true});
 // Return to deterministic checks and complete v4.
 await page.getByRole('button',{name:'Revise agent',exact:true}).click();await next(page);await next(page);
 await selectOption(page,'Evaluation profile','Rule-based checks');
 await next(page);await page.getByRole('button',{name:'Create & test agent'}).click();
 await expect(page.getByText('Sample test passed',{exact:true}).first()).toBeVisible();
 await page.getByRole('tab',{name:'Versions and source'}).click();
 await expect(page.getByRole('cell',{name:'v4',exact:true}).first()).toBeVisible();
 // Request existing capability; Admin must decide; business sees effective result.
 // Catalog UI request is covered by zz-ai-catalog; seed this legacy fixture through its authenticated API.
 const identity=await (await page.request.get('/api/me')).json();
 const accessRequest=await page.request.post('/api/requests',{data:{component_id:'restricted-insights',reason:'Need synthetic strategy evidence for research briefs.'},headers:{'X-CSRF-Token':identity.csrf,origin:'http://127.0.0.1:5188'}});
 expect(accessRequest.status()).toBe(201);
 await switchPersona(page,'Platform Admin');
 await nav(page,'Policies & approvals');
 await page.getByRole('textbox',{name:/Decision reason for restricted-insights/}).fill('Approved for synthetic research use.');
 await page.getByRole('button',{name:'Approve access',exact:true}).click();
 await expect(page.getByText('Approved',{exact:true})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-admin-approval.png',fullPage:true});
 await switchPersona(page,'Alex Morgan');
 const approvedRequests=await (await page.request.get('/api/requests')).json();
 expect(approvedRequests.some((r:any)=>r.component==='restricted-insights'&&r.status==='APPROVED')).toBe(true);
 await nav(page,'Create agent');
 await page.getByRole('radio',{name:'Select Research brief'}).click();await next(page);
 await selectOption(page,'Model route','Claude · Bedrock');
 await page.getByRole('button',{name:/^Tools \/ MCP/}).click();
 await expect(page.getByRole('option').filter({hasText:'Synthetic strategy insights'})).toBeVisible();
 await page.keyboard.press('Escape');
 // A passing old version must be blocked immediately after admin revocation.
 await switchPersona(page,'Platform Admin');
 await nav(page,'Tools & skills');
 const grant=page.getByRole('checkbox',{name:'Alex Morgan access to Synthetic knowledge search',exact:true});
 await expect(grant).toBeChecked();await grant.click();await expect(grant).not.toBeChecked();
 await switchPersona(page,'Alex Morgan');
 await page.getByRole('link',{name:'Aurora research companion',exact:true}).click();
 await page.getByRole('tab',{name:'Run sample',exact:true}).click();
 await expect(page.getByRole('button',{name:'Run current version'})).toBeEnabled();
 await page.getByRole('button',{name:'Run current version'}).click();
 await expect(page.getByText('Component not authorized or compatible: synthetic-search',{exact:true})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-revocation-blocked.png',fullPage:true});
 expect(errors).toEqual([]);
});

test('Cloudscape filtered persona, foundation reset, admin revoke and responsive layout',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 await expect(page.getByText('No agents yet',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('radio',{name:'Select Research brief'}).click();await next(page);
 await page.getByRole('button',{name:/^Model route/}).click();
 await expect(page.getByRole('option').filter({hasText:'Claude · Bedrock'})).toBeVisible();
 await expect(page.getByRole('option').filter({hasText:'OpenAI · Bedrock'})).toHaveCount(0);
 await expect(page.getByRole('option').filter({hasText:'Gemini'})).toHaveCount(0);
 await page.getByRole('option').filter({hasText:'Claude · Bedrock'}).click();
 await page.getByRole('button',{name:'Previous',exact:true}).click();
 await page.getByRole('radio',{name:'Select Knowledge Q&A'}).click();await next(page);
 await expect(page.getByRole('button',{name:/^Model route/})).toContainText('Select an approved model');
 await page.setViewportSize({width:390,height:844});
 await expect(page.getByRole('heading',{name:'Choose capabilities',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBeTruthy();
 await page.screenshot({path:'../artifacts/cloudscape-mobile.png',fullPage:true});
});
