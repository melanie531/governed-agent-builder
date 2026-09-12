import {test,expect} from '@playwright/test';
import fs from 'node:fs';
const tools=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/aws-documentation-tools.json',import.meta.url),'utf8')) as {tools:{name:string;description:string;inputSchema:unknown}[]};
test('five resource categories, actual declared operations, stable draft selection and back navigation (offline)',async({page})=>{
 const base={version:'7',provider:'Offline native adapter',approved:true,external:false,fixture:false,capabilities:[],data_handling:'Public metadata',status:'requestable',policy_reason:'Grant required',requestable:true,usable:false,execution_ready:false,origin:'Synthetic identity',refreshed_at:null};
 const parent={...base,id:'registry:synthetic:documentation',record_id:'registry:synthetic:documentation',kind:'mcp_server',protocol:'MCP',name:'AWS documentation',description:'Public AWS documentation'};
 const children=tools.tools.map(t=>({...base,...t,id:parent.id+':tool:'+t.name,record_id:parent.id+':tool:'+t.name,operation:t.name,kind:'tool',parent_id:parent.id,protocol:'MCP'}));
 const foundation={id:'research',name:'Research brief',description:'Offline foundation',version:'1',approved:true,mandatory_defaults:[]};
 const dataset=[{id:'qa-case',input:'Synthetic question',required_terms:[],require_citation:false,expect_refusal:false,expected_format:'text'}];
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[parent,...children,{...parent,id:'custom',kind:'resource',protocol:'CUSTOM',name:'Unsupported custom',supported:false}],count:2,mode:'live',sources:{Registry:{connection_state:'connected'},ModelGateway:{connection_state:'NotConnected'}}}}));
 await page.route('**/api/build-options*',r=>r.fulfill({json:{foundations:[foundation],choices:{models:[],tools:children,skills:[]},sample_dataset:dataset,catalog_mode:'live'}}));
 await page.goto('/');await page.getByRole('button',{name:'Enter as Alex Morgan'}).click();
 await page.getByRole('button',{name:'Create agent',exact:true}).click();await page.getByRole('radio',{name:'Select Research brief'}).click();await page.getByRole('button',{name:'Next',exact:true}).click();await page.getByRole('button',{name:'Next',exact:true}).click();
 await page.getByRole('textbox',{name:/^Agent name/}).fill('Offline QA draft');
 await page.getByRole('textbox',{name:/^Your instructions/}).fill('Preserve the exact QA prompt.');
 await page.getByRole('textbox',{name:/^Success criteria/}).fill('Preserve QA evaluation.');
 const nav=async()=>{const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();await page.getByRole('link',{name:'AI Catalog',exact:true}).click();};
 await nav();await expect(page.getByRole('tab')).toHaveText(['Models','MCP servers','Skills','Agents','Other']);await expect(page.getByRole('tab',{name:'Tools',exact:true})).toHaveCount(0);
 await expect(page.getByText('Models: NotConnected. See Connection details.',{exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'MCP servers',exact:true}).click();await expect(page.getByText('5 tools',{exact:true})).toBeVisible();
 for(const width of [1365,390]){
  await page.setViewportSize({width,height:1080});
  const row=page.getByRole('row').filter({has:page.getByRole('button',{name:parent.name,exact:true})});
  await expect.poll(()=>row.getByRole('cell').first().evaluate(e=>e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(240);
  await expect.poll(()=>row.getByRole('cell').last().evaluate(e=>e.getBoundingClientRect().width)).toBeGreaterThanOrEqual(160);
 }
 await page.setViewportSize({width:1365,height:1080});
 await page.getByRole('button',{name:'View details',exact:true}).click();for(const t of tools.tools)await expect(page.getByText(t.name,{exact:true})).toBeVisible();
 await page.getByRole('dialog').evaluate(async el=>{await Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})));});
 const surface=page.getByRole('dialog').locator('[class*="awsui_container_"]').first();
 await expect(surface).toHaveCSS('background-color','rgb(255, 255, 255)');await expect(surface).toHaveCSS('opacity','1');
 await page.getByRole('button',{name:'Back to catalog'}).click();await page.getByRole('button',{name:'View details',exact:true}).click();
 await page.getByRole('button',{name:'Add to draft (not deployment)',exact:true}).first().click();await expect(page.getByText(children[0].id+' · 7',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Next',exact:true}).click();await expect(page.getByRole('textbox',{name:/^Your instructions/})).toHaveValue('Preserve the exact QA prompt.');await expect(page.getByRole('textbox',{name:/^Success criteria/})).toHaveValue('Preserve QA evaluation.');await expect(page.getByRole('textbox',{name:/^Evaluation dataset/})).toHaveValue(/qa-case/);
 await page.getByRole('button',{name:'Next',exact:true}).click();await expect(page.getByRole('button',{name:'Save draft',exact:true})).toBeVisible();
});
