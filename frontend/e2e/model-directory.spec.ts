import {test,expect} from '@playwright/test';
// Backend-filtered discovery records, synthetic local data; not live evidence.
test('backend models grouped by provider and filtered by modality without rewriting IDs',async({page})=>{
 const rows=[['a','Anthropic',['input:Text','output:Text']],['b','OpenAI',['input:Image','output:Text']]].map(([id,provider,capabilities])=>({id,record_id:id,kind:'model',name:`Reviewed model ${id}`,provider,capabilities,release_date:'2026-08-12',source_url:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html',description:'Reviewed capability description',status:'discovery_only',discovery_only:true,requestable:false,usable:false,execution_ready:false,fixture:false,version:'synthetic-revision',data_handling:'Not assessed'}));
 await page.route('**/studio-config.json',r=>r.fulfill({json:{hosted:true,mode:'hosted'}}));
 await page.route('**/api/me',r=>r.fulfill({json:{persona:{id:'qa',name:'QA',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic'}}));
 await page.route('**/api/agents',r=>r.fulfill({json:[]}));
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:rows,count:2,mode:'live',agent_listing_implemented:true}}));
 await page.goto('/');await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Anthropic (1)',exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'OpenAI (1)',exact:true})).toBeVisible();
 await page.getByLabel('Filter models by capability',{exact:true}).selectOption('input:Image');
 await expect(page.getByRole('button',{name:'Reviewed model a',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Reviewed model b',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('Released: 2026-08-12',{exact:true})).toBeVisible();
 await expect(dialog.getByRole('button',{name:'Add to draft',exact:true})).toHaveCount(0);
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('Catalog record: b',{exact:true})).toBeVisible();
});

// Task 3 (哥哥 refinement 2): the UI must show the CURRENT USER's real,
// deduplicated authorization from the API access_summary — granted / callable /
// requestable as distinct counts — NOT the catalog total.
test('access summary reflects per-user granted/callable/requestable, not the catalog total',async({page})=>{
 const models=[
  {id:'m-granted',record_id:'m-granted',kind:'model',name:'Granted model',provider:'Anthropic',capabilities:['input:text'],description:'d',status:'available',usable:true,granted:true,requestable:false,execution_ready:false,fixture:false,version:'1',data_handling:'x'},
  {id:'m-req',record_id:'m-req',kind:'model',name:'Requestable model',provider:'OpenAI',capabilities:['input:text'],description:'d',status:'requestable',usable:false,granted:false,requestable:true,execution_ready:false,fixture:false,version:'1',data_handling:'x'},
 ];
 await page.route('**/studio-config.json',r=>r.fulfill({json:{hosted:true,mode:'hosted'}}));
 await page.route('**/api/me',r=>r.fulfill({json:{persona:{id:'qa',name:'QA',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic'}}));
 await page.route('**/api/agents',r=>r.fulfill({json:[]}));
 // access_summary is the REAL per-user set; count (catalog total) is deliberately larger.
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:models,count:42,mode:'live',agent_listing_implemented:true,access_summary:{granted:1,requestable:1,callable:0,available:42}}}));
 await page.goto('/');await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 const summary=page.getByTestId('access-summary');
 await expect(summary).toBeVisible();
 // Shows the per-user granted (1), callable (0) and requestable (1) — never the total (42).
 await expect(summary).toContainText('1 granted');
 await expect(summary).toContainText('0 callable');
 await expect(summary).toContainText('1 available to request');
 await expect(summary).not.toContainText('42 granted');
});
