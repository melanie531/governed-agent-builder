import {test,expect} from '@playwright/test';

// Presentation-only contract for the AI Catalog Models list (mocked /api/catalog):
// the verified Haiku route displays "Anthropic" / "Claude Haiku 4.5"; the full
// model/profile ID appears only inside View details. Unknown IDs are never
// relabelled as Haiku. Access/readiness/approval semantics are untouched.
const base={record_id:'',version:'v1',kind:'model',description:'Enumerated Bedrock inference route; execution not verified',provider:'Amazon Bedrock',data_handling:'Synthetic',capabilities:[],status:'AVAILABLE',policy_reason:'',requestable:false,usable:true,origin:'AgentCore Model Gateway',refreshed_at:null,fixture:false,approved:true,external:false,execution_ready:false};
const haiku={...base,id:'model:gw:target:us.anthropic.claude-haiku-4-5-20251001-v1:0',record_id:'model:gw:target:us.anthropic.claude-haiku-4-5-20251001-v1:0',name:'demo-target/us.anthropic.claude-haiku-4-5-20251001-v1:0',model_id:'demo-target/us.anthropic.claude-haiku-4-5-20251001-v1:0'};
const unknown={...base,id:'model:gw:target:us.anthropic.claude-mystery-9-9-v1:0',record_id:'model:gw:target:us.anthropic.claude-mystery-9-9-v1:0',name:'demo-target/us.anthropic.claude-mystery-9-9-v1:0',model_id:'demo-target/us.anthropic.claude-mystery-9-9-v1:0',status:'PENDING',usable:false,requestable:true};

test('Models list shows Anthropic / Claude Haiku 4.5; full ID only in View details; unknown IDs never relabelled',async({page})=>{
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[haiku,unknown],count:2,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 // List: verified Haiku shows friendly provider/name, not the raw route ID.
 await expect(page.getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toBeVisible();
 await expect(page.getByText('Anthropic',{exact:true})).toBeVisible();
 const table=page.getByRole('table');
 await expect(table.getByText('demo-target/us.anthropic.claude-haiku-4-5-20251001-v1:0',{exact:true})).toHaveCount(0);
 // Unknown route keeps its raw identity; it is never presented as Haiku.
 await expect(page.getByRole('button',{name:'demo-target/us.anthropic.claude-mystery-9-9-v1:0'})).toBeVisible();
 // Readiness/access markers unchanged.
 await expect(table.getByText('Not ready',{exact:true})).toHaveCount(2);
 await expect(table.getByText('Pending approval',{exact:true})).toBeVisible();
 // View details: full model/profile ID present.
 await page.getByRole('button',{name:'Claude Haiku 4.5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByRole('heading',{name:'Claude Haiku 4.5'})).toBeVisible();
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('demo-target/us.anthropic.claude-haiku-4-5-20251001-v1:0').first()).toBeVisible();
 await expect(dialog.getByText('Provider: Anthropic',{exact:true})).toBeVisible();
});
