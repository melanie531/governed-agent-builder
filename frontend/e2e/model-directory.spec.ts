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
