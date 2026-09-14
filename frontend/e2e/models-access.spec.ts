import {test,expect} from '@playwright/test';

// Access-UX contract for the Models catalog (mocked /api/catalog; field shapes
// mirror the REAL live payload captured 2026-09-13: discovery entries carry
// discovery_only:true, status:'blocked' (a pure usable/requestable mapping,
// NOT a permission denial), granted:false, requestable:false,
// execution_ready:false; the legacy Haiku gateway binding has NO recency field).
//
// These are MOCK tests: they prove the frontend mapping only. Real authorization
// facts come from the separate read-only live QA check (see RESULT.md).
//
// User-directed behaviour under test:
// - Recent / Not recent / Pending verification badges and the recency
//   SegmentedControl are REMOVED from the UI (backend 6-month rule unchanged).
// - Default Models list shows only recent discovery items; out-of-window /
//   pending discovery items are not mixed in.
// - Legacy (non-discovery) bindings are kept in a clearly separate
//   "Previously configured models" section, never mixed into the new list.
// - discovery-only entries read "Not configured", NEVER "Blocked"/denied.
// - granted===true reads "Access granted" (independent of execution Ready).
// - The page states the REAL count of models with access granted; when every
//   grant is false the count is honestly 0.

const base={record_id:'',version:'1',kind:'model',data_handling:'Discovery metadata only',capabilities:['input:text','output:text'],policy_reason:'',origin:'Bedrock foundation-model discovery',refreshed_at:null,fixture:false,approved:true,external:false,provenance:'bedrock:ListFoundationModels'};

const recentNoAccess={...base,id:'discovery:bedrock:anthropic.claude-opus-5',record_id:'discovery:bedrock:anthropic.claude-opus-5',
 name:'Claude Opus 5',provider:'Anthropic',model_id:'anthropic.claude-opus-5',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 recency:'recent',launch_date:'2026-07-24',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5.html',
 discovery_only:true,status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};

const outOfWindow={...base,id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',record_id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',
 name:'Claude Sonnet 4',provider:'Anthropic',model_id:'anthropic.claude-sonnet-4-20250514-v1:0',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 recency:'out_of_window',launch_date:'2025-05-23',
 discovery_only:true,status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};

// SYNTHETIC granted entry (mock only): the live account currently has ZERO
// granted models. This exists purely to test the granted===true mapping.
const grantedModel={...base,id:'discovery:bedrock:openai.gpt-6-astra',record_id:'discovery:bedrock:openai.gpt-6-astra',
 name:'GPT-6 Astra',provider:'OpenAI',model_id:'openai.gpt-6-astra',
 description:'OpenAI foundation model discovered via bedrock:ListFoundationModels.',
 recency:'recent',launch_date:'2026-09-08',
 discovery_only:false,status:'available',usable:true,granted:true,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};

// Legacy gateway binding: NO recency field (not a discovery record).
const legacyHaiku={...base,id:'model:demo-gateway-record:tgt0000001:us.anthropic.claude-haiku-4-5-20251001-v1:0',
 record_id:'model:demo-gateway-record:tgt0000001:us.anthropic.claude-haiku-4-5-20251001-v1:0',
 name:'us.anthropic.claude-haiku-4-5-20251001-v1:0',provider:'Amazon Bedrock',
 model_id:'us.anthropic.claude-haiku-4-5-20251001-v1:0',target_id:'tgt0000001',connector:'bedrock-runtime passthrough',
 version:'0efbe171d23945bcbf817b1620fdcbd45ec1a5cd0456890036eae214cea03cb8',
 description:'Approved Runtime route; execution authorization checked separately',
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};

const items=[recentNoAccess,outOfWindow,grantedModel,legacyHaiku];

async function openCatalog(page,rows,summary?:{granted:number;available:number;callable:number;requestable:number}){
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:rows,count:rows.length,mode:'live',agent_listing_implemented:true,...(summary?{access_summary:summary}:{})}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 await expect(page.getByRole('button',{name:'Sam Taylor · Operations desk',exact:true})).toBeVisible();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 await expect(page.getByRole('button',{name:'Claude Opus 5',exact:true})).toBeVisible();
}

test('recency badges and time filter are removed; default list shows only recent discovery models',async({page})=>{
 await openCatalog(page,items);
 // No recency badges anywhere in the models view.
 await expect(page.getByText('Recent',{exact:true})).toHaveCount(0);
 await expect(page.getByText('Not recent',{exact:true})).toHaveCount(0);
 await expect(page.getByText('Pending verification',{exact:true})).toHaveCount(0);
 // The SegmentedControl time filter is gone.
 await expect(page.getByRole('button',{name:'Recent (last 6 months)',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'All discovered',exact:true})).toHaveCount(0);
 // Out-of-window discovery model is not mixed into the default list.
 await expect(page.getByRole('button',{name:'Claude Sonnet 4',exact:true})).toHaveCount(0);
 // Recent discovery models render under their provider group.
 await expect(page.getByRole('table',{name:'Anthropic'}).getByRole('button',{name:'Claude Opus 5',exact:true})).toBeVisible();
});

test('discovery-only reads Not configured (never Blocked); granted reads Access granted with Ready kept separate',async({page})=>{
 await openCatalog(page,items);
 const anthropic=page.getByRole('table',{name:'Anthropic'});
 const openai=page.getByRole('table',{name:'OpenAI'});
 // Discovery-only, not connected: catalog availability, not a permission denial.
 await expect(anthropic.getByText('Not configured',{exact:true})).toBeVisible();
 await expect(page.getByText('Blocked',{exact:true})).toHaveCount(0);
 await expect(page.getByText('blocked',{exact:true})).toHaveCount(0);
 // granted===true -> Access granted, while execution readiness stays Not ready.
 await expect(openai.getByText('Access granted',{exact:true})).toBeVisible();
 await expect(openai.getByText('Not ready',{exact:true})).toBeVisible();
 await expect(openai.getByText('Ready',{exact:true})).toHaveCount(0);
});

test('legacy binding remains in backend payload but not default model list',async({page})=>{
 await openCatalog(page,items);
 await expect(page.getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toHaveCount(0);
 const payload=await page.evaluate(async()=> (await fetch('/api/catalog')).json());
 expect(payload.items.find(x=>x.id===legacyHaiku.id).model_id).toBe(legacyHaiku.model_id);
});

test('access count states the real number of granted models, including an honest zero',async({page})=>{
 await openCatalog(page,items);
 await expect(page.getByText('Access granted: 1 of 2 models',{exact:true}).first()).toBeVisible();
});

test('same-model routes deduplicate; requestable and MCP do not increase grants',async({page})=>{
 const duplicate={...grantedModel,id:'second-route',record_id:'second-route'};
 const requestable={...recentNoAccess,id:'requestable',model_id:'requestable-model',name:'Requestable example',requestable:true};
 const mcp={...grantedModel,id:'server',kind:'mcp_server',protocol:'MCP',supported:true};
 await openCatalog(page,[...items,duplicate,requestable,mcp]);
 await expect(page.getByText('Access granted: 1 of 3 models',{exact:true})).toBeVisible();
});

test('server identity association summary is used without granting the discovery row',async({page})=>{
 await openCatalog(page,[recentNoAccess,legacyHaiku],{granted:1,available:1,callable:0,requestable:0});
 await expect(page.getByText('Access granted: 1 of 1 models',{exact:true})).toBeVisible();
 await expect(page.getByText('Not configured',{exact:true})).toBeVisible();
});

test('all grants false -> count is 0, no invented access',async({page})=>{
 const none=items.map(x=>({...x,granted:false,usable:false,status:x.discovery_only?'blocked':x.status==='available'?'blocked':x.status}));
 await openCatalog(page,none);
 await expect(page.getByText('Access granted: 0 of 2 models',{exact:true}).first()).toBeVisible();
 await expect(page.getByText('Access granted',{exact:true})).toHaveCount(0);
});
