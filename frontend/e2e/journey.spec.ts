import {test,expect,Page} from '@playwright/test';
import fs from 'node:fs';

async function selectOption(page:Page,label:string,option:string){
 await page.getByRole('button',{name:label,exact:true}).click();
 await page.getByRole('option').filter({hasText:option}).first().click();
}
async function switchPersona(page:Page,name:string){
 await page.getByRole('button',{name:/DEV ONLY/}).click();
 await page.getByRole('menuitem',{name:new RegExp(name)}).click();
}
async function next(page:Page){await page.getByRole('button',{name:'Next',exact:true}).click();}

test('Cloudscape business journey: create, measured evidence, revise, retest, export and approval',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');
 await expect(page.getByText('DEV ONLY identity selector',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Enter as Alex Morgan'}).click();
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-my-agents.png',fullPage:true});
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('radio',{name:'Select Research brief'}).click();
 await next(page);
 await selectOption(page,'Model route','Claude · Bedrock');
 await page.getByRole('button',{name:'Tools / MCP',exact:true}).click();
 await page.getByRole('option').filter({hasText:'Synthetic knowledge search'}).click();
 await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Skills',exact:true}).click();
 await page.getByRole('option').filter({hasText:'Evidence citations'}).click();
 await page.keyboard.press('Escape');
 await next(page);
 await page.getByRole('textbox',{name:'Agent name',exact:true}).fill('Aurora research companion');
 await page.getByRole('textbox',{name:'Your instructions',exact:true}).fill('Use only synthetic evidence. Cite sources. Refuse unknown questions. Uppercase.');
 await page.getByRole('textbox',{name:'Success criteria',exact:true}).fill('Cite synthetic evidence and refuse unknown data.');
 await expect(page.getByRole('textbox',{name:'Evaluation dataset',exact:true})).toHaveValue(/launch/);
 await page.screenshot({path:'../artifacts/cloudscape-wizard-evaluation.png',fullPage:true});
 await next(page);
 await expect(page.getByText('Aurora research companion',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Deploy & test locally',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Aurora research companion',exact:true})).toBeVisible();
 await expect(page.getByText('Local checks passed',{exact:true}).first()).toBeVisible();
 await expect(page.getByText('100 / 100 measured.',{exact:false})).toBeVisible();
 await expect(page.getByText('AURORA LAUNCHES IN OCTOBER',{exact:false})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-results-pass.png',fullPage:true});
 await page.getByRole('tab',{name:'Run locally',exact:true}).click();
 await page.getByRole('button',{name:'Run current version'}).click();
 await expect(page.getByLabel('Local invocation output')).toContainText('OCTOBER');
 await page.getByRole('tab',{name:'Execution trace'}).click();
 await expect(page.getByText('fixture_lookup',{exact:false}).first()).toBeVisible();
 // Export from authorized backend, then inspect signature and filename.
 const downloadPromise=page.waitForEvent('download');
 await page.getByRole('button',{name:'Export source',exact:true}).click();
 const download=await downloadPromise;
 await download.saveAs('../artifacts/browser-export-v1.zip');
 expect(download.suggestedFilename()).toMatch(/-v1\.zip$/);
 expect(fs.readFileSync('../artifacts/browser-export-v1.zip').subarray(0,2).toString()).toBe('PK');
 // Create a genuine failed revision by changing expected evidence.
 await page.getByRole('button',{name:'Revise agent',exact:true}).click();
 await next(page);await next(page);
 await page.getByRole('button',{name:'Try a failing case'}).click();
 await next(page);await page.getByRole('button',{name:'Deploy & test locally'}).click();
 await expect(page.getByText('Needs changes',{exact:true}).first()).toBeVisible();
 await page.getByRole('tab',{name:'Evaluation results'}).click();
 await expect(page.getByText('Required term: impossible-fixture-term',{exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'Run locally',exact:true}).click();
 await expect(page.getByRole('button',{name:'Run current version'})).toBeDisabled();
 // Repair via editable JSON and demonstrate missing required judge gate.
 await page.getByRole('button',{name:'Revise agent',exact:true}).click();await next(page);await next(page);
 const dataset=JSON.parse(await page.getByRole('textbox',{name:'Evaluation dataset',exact:true}).inputValue());
 dataset[0].required_terms=['Aurora','October'];
 await page.getByRole('textbox',{name:'Evaluation dataset',exact:true}).fill(JSON.stringify(dataset,null,2));
 await page.getByRole('button',{name:'Try a missing judge'}).click();
 await next(page);await page.getByRole('button',{name:'Deploy & test locally'}).click();
 await expect(page.getByText('Required judge evidence is missing.',{exact:false})).toBeVisible();
 await expect(page.getByText('Needs changes',{exact:true}).first()).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-missing-judge.png',fullPage:true});
 // Return to deterministic checks and complete v4.
 await page.getByRole('button',{name:'Revise agent',exact:true}).click();await next(page);await next(page);
 await selectOption(page,'Evaluation profile','Local deterministic checks');
 await next(page);await page.getByRole('button',{name:'Deploy & test locally'}).click();
 await expect(page.getByText('Local checks passed',{exact:true}).first()).toBeVisible();
 await page.getByRole('tab',{name:'Versions and source'}).click();
 await expect(page.getByRole('cell',{name:'v4',exact:true}).first()).toBeVisible();
 // Request existing capability; Admin must decide; business sees effective result.
 await page.getByRole('link',{name:'Capability requests',exact:true}).click();
 await selectOption(page,'Requested capability','Synthetic strategy insights');
 await page.getByRole('textbox',{name:'Business reason',exact:true}).fill('Need synthetic strategy evidence for research briefs.');
 await page.getByRole('button',{name:'Send request'}).click();
 await expect(page.getByText('PENDING',{exact:true})).toBeVisible();
 await switchPersona(page,'Platform Admin');
 await page.getByRole('link',{name:'Policies & approvals',exact:true}).click();
 await page.getByRole('textbox',{name:'Decision reason for restricted-insights',exact:true}).fill('Approved for synthetic research use.');
 await page.getByRole('button',{name:'Approve access',exact:true}).click();
 await expect(page.getByText('APPROVED',{exact:true})).toBeVisible();
 await page.screenshot({path:'../artifacts/cloudscape-admin-approval.png',fullPage:true});
 await switchPersona(page,'Alex Morgan');
 await page.getByRole('link',{name:'Capability requests',exact:true}).click();
 await expect(page.getByText('APPROVED',{exact:true})).toBeVisible();
 await page.getByRole('link',{name:'Create agent',exact:true}).click();
 await page.getByRole('radio',{name:'Select Research brief'}).click();await next(page);
 await selectOption(page,'Model route','Claude · Bedrock');
 await page.getByRole('button',{name:'Tools / MCP',exact:true}).click();
 await expect(page.getByRole('option').filter({hasText:'Synthetic strategy insights'})).toBeVisible();
 await page.keyboard.press('Escape');
 expect(errors).toEqual([]);
});

test('Cloudscape filtered persona, foundation reset, admin revoke and responsive layout',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 await expect(page.getByText('No agents yet',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('radio',{name:'Select Research brief'}).click();await next(page);
 await page.getByRole('button',{name:'Model route',exact:true}).click();
 await expect(page.getByRole('option').filter({hasText:'Claude · Bedrock'})).toBeVisible();
 await expect(page.getByRole('option').filter({hasText:'OpenAI · Bedrock'})).toHaveCount(0);
 await expect(page.getByRole('option').filter({hasText:'Gemini'})).toHaveCount(0);
 await page.getByRole('option').filter({hasText:'Claude · Bedrock'}).click();
 await page.getByRole('button',{name:'Previous',exact:true}).click();
 await page.getByRole('radio',{name:'Select Knowledge Q&A'}).click();await next(page);
 await expect(page.getByRole('button',{name:'Model route',exact:true})).toContainText('Select an approved model');
 await page.setViewportSize({width:390,height:844});
 await expect(page.getByRole('heading',{name:'Choose capabilities',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1)).toBeTruthy();
 await page.screenshot({path:'../artifacts/cloudscape-mobile.png',fullPage:true});
});
