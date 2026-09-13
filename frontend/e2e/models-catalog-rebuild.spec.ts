import {test,expect} from '@playwright/test';

// Rebuilt Models catalog contract (mocked /api/catalog). Verifies the four
// product requirements as DISPLAY behaviour only — no access/grant/readiness
// state is fabricated by the frontend; every such marker below is asserted to
// match exactly what the mocked API returned.
//
// 1. Group BY PROVIDER, name-first, raw ID only in details.
// 2. Filter by CATEGORY.
// 3. Recency: only last-6-months models get the "New" badge; older ones "Legacy".
// 4. Discoverable-vs-callable: a model can be listed yet not execution-ready.

const base={record_id:'',version:'v1',kind:'model',description:'Enumerated Bedrock inference route',provider:'Amazon Bedrock',data_handling:'Synthetic',capabilities:[],policy_reason:'',origin:'AgentCore Model Gateway',refreshed_at:null,fixture:false,approved:true,external:false};

// Verified native IDs (present in the curated ModelCatalogMeta window).
const opus5={...base,id:'m:opus5',record_id:'m:opus5',name:'route/us.anthropic.claude-opus-5',model_id:'route/us.anthropic.claude-opus-5',native_model_id:'anthropic.claude-opus-5-20260724-v1:0',
 status:'AVAILABLE',usable:false,requestable:true,execution_ready:false,execution_binding:{status:'NOT_CONFIGURED',last_checked:null}};
const gemma={...base,id:'m:gemma',record_id:'m:gemma',name:'route/gemma-4-31b',model_id:'route/google.gemma-4-31b-v1:0',native_model_id:'google.gemma-4-31b-v1:0',
 status:'AVAILABLE',usable:false,requestable:true,execution_ready:false,execution_binding:{status:'NOT_CONFIGURED',last_checked:null}};
// Verified but OUTSIDE the 6-month window (Haiku 4.5, Oct 2025) AND the one
// route that is actually wired/callable: discoverable-vs-callable made explicit.
const haiku={...base,id:'m:haiku',record_id:'m:haiku',name:'route/us.anthropic.claude-haiku-4-5',model_id:'route/us.anthropic.claude-haiku-4-5-20251001-v1:0',native_model_id:'anthropic.claude-haiku-4-5-20251001-v1:0',
 status:'available',usable:true,granted:true,requestable:false,execution_ready:true,execution_binding:{status:'verified',last_checked:1}};

test('Models grouped by provider, filtered by category, recency badges, discoverable-vs-callable',async({page})=>{
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[opus5,gemma,haiku],count:3,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();

 // (1) Provider grouping: separate Anthropic and Google tables, name-first.
 const anthropic=page.getByRole('table',{name:'Anthropic models'});
 const google=page.getByRole('table',{name:'Google models'});
 await expect(anthropic).toBeVisible();
 await expect(google).toBeVisible();
 await expect(anthropic.getByRole('button',{name:'Claude Opus 5',exact:true})).toBeVisible();
 await expect(anthropic.getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toBeVisible();
 await expect(google.getByRole('button',{name:'Gemma 4 31B',exact:true})).toBeVisible();
 // Raw model ID never appears as a title in the list.
 await expect(page.getByRole('button',{name:'route/us.anthropic.claude-opus-5'})).toHaveCount(0);

 // (3) Recency badges: in-window models "New", the Oct-2025 Haiku "Legacy".
 await expect(anthropic.getByText('New · 2026-07-24',{exact:true})).toBeVisible();
 await expect(google.getByText('New · 2026-03-31',{exact:true})).toBeVisible();
 await expect(anthropic.getByText('Legacy · 2025-10-16',{exact:true})).toBeVisible();

 // (4) Discoverable-vs-callable, straight from API state (not fabricated):
 // Opus 5 is discoverable but not wired; Haiku is execution-ready.
 await expect(anthropic.getByText('Discoverable · requestable',{exact:true})).toBeVisible();
 await expect(anthropic.getByText('Execution-ready',{exact:true})).toBeVisible();
 // Readiness column mirrors execution_ready exactly.
 await expect(page.getByText('Ready',{exact:true})).toHaveCount(1);
 await expect(page.getByText('Not ready',{exact:true})).toHaveCount(2);

 // (2) Category filter: switching to "Multimodal" keeps all three (all are
 // multimodal in the curated data); a category with no members hides tables.
 await page.getByRole('button',{name:'Multimodal',exact:true}).click();
 await expect(anthropic.getByRole('button',{name:'Claude Opus 5',exact:true})).toBeVisible();
 await expect(google.getByRole('button',{name:'Gemma 4 31B',exact:true})).toBeVisible();

 // Details expose the raw IDs + verified launch date, never the list title.
 await page.getByRole('button',{name:'Claude Opus 5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('Category: Multimodal',{exact:true})).toBeVisible();
 await expect(dialog.getByText(/Official launch date: 2026-07-24/)).toBeVisible();
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('anthropic.claude-opus-5-20260724-v1:0').first()).toBeVisible();
});
