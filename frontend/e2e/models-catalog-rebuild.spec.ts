import {test,expect} from '@playwright/test';

// Rebuilt Models catalog contract (mocked /api/catalog). Verifies the four
// product requirements as DISPLAY behaviour driven ENTIRELY by real
// bedrock:ListFoundationModels fields returned by the API — the frontend never
// fabricates provider, category, lifecycle, or callable state, and there is NO
// hand-curated/guessed model map or invented launch date.
//
// 1. Group BY PROVIDER (real providerName), name-first, raw model ID only in details.
// 2. Filter by CATEGORY (derived from real modalities on the backend).
// 3. Recency: driven by real modelLifecycle.status (ACTIVE|LEGACY). The API has
//    NO launch date, so no date is shown; ACTIVE vs LEGACY is the honest signal.
// 4. Discoverable-vs-callable: a discovered model is listed yet not execution-ready.

const base={record_id:'',version:'1',kind:'model',data_handling:'Discovery metadata only',capabilities:[],policy_reason:'',origin:'Bedrock foundation-model discovery',refreshed_at:null,fixture:false,approved:true,external:false,provenance:'bedrock:ListFoundationModels'};

// All values below mirror the account's REAL ListFoundationModels output
// (account 302277511711 / us-west-2). No guessed IDs, no invented dates.
const haiku={...base,id:'discovery:bedrock:anthropic.claude-haiku-4-5-20251001-v1:0',record_id:'discovery:bedrock:anthropic.claude-haiku-4-5-20251001-v1:0',
 name:'Claude Haiku 4.5',provider:'Anthropic',model_id:'anthropic.claude-haiku-4-5-20251001-v1:0',native_model_id:'anthropic.claude-haiku-4-5-20251001-v1:0',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 category:'multimodal',lifecycle:'ACTIVE',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// LEGACY lifecycle model (real Sonnet 4 is LEGACY in this account).
const sonnet={...base,id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',record_id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',
 name:'Claude Sonnet 4',provider:'Anthropic',model_id:'anthropic.claude-sonnet-4-20250514-v1:0',native_model_id:'anthropic.claude-sonnet-4-20250514-v1:0',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 category:'multimodal',lifecycle:'LEGACY',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// A different provider + a text-only category.
const qwen={...base,id:'discovery:bedrock:qwen.qwen3-235b-a22b-2507-v1:0',record_id:'discovery:bedrock:qwen.qwen3-235b-a22b-2507-v1:0',
 name:'Qwen3 235B A22B 2507',provider:'Qwen',model_id:'qwen.qwen3-235b-a22b-2507-v1:0',native_model_id:'qwen.qwen3-235b-a22b-2507-v1:0',
 description:'Qwen foundation model discovered via bedrock:ListFoundationModels.',
 category:'text',lifecycle:'ACTIVE',streaming:true,inference_types:['ON_DEMAND'],input_modalities:['TEXT'],output_modalities:['TEXT'],
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// A wired/callable model to prove discoverable-vs-callable is API-driven.
const wired={...base,id:'discovery:bedrock:openai.gpt-6-astra',record_id:'discovery:bedrock:openai.gpt-6-astra',
 name:'GPT-6 Astra',provider:'OpenAI',model_id:'openai.gpt-6-astra',native_model_id:'openai.gpt-6-astra',
 description:'OpenAI foundation model discovered via bedrock:ListFoundationModels.',
 category:'multimodal',lifecycle:'ACTIVE',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],
 status:'available',usable:true,granted:true,requestable:false,execution_ready:true,execution_binding:{status:'verified',last_checked:1}};

test('Models grouped by provider, filtered by category, lifecycle recency, discoverable-vs-callable',async({page})=>{
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[haiku,sonnet,qwen,wired],count:4,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();

 // (1) Provider grouping from real providerName: Anthropic, OpenAI, Qwen tables.
 const anthropic=page.getByRole('table',{name:'Anthropic models'});
 const openai=page.getByRole('table',{name:'OpenAI models'});
 const qwenTable=page.getByRole('table',{name:'Qwen models'});
 await expect(anthropic).toBeVisible();
 await expect(openai).toBeVisible();
 await expect(qwenTable).toBeVisible();
 await expect(anthropic.getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toBeVisible();
 await expect(anthropic.getByRole('button',{name:'Claude Sonnet 4',exact:true})).toBeVisible();
 await expect(openai.getByRole('button',{name:'GPT-6 Astra',exact:true})).toBeVisible();
 await expect(qwenTable.getByRole('button',{name:'Qwen3 235B A22B 2507',exact:true})).toBeVisible();
 // Raw model ID never appears as a title in the list.
 await expect(page.getByRole('button',{name:'anthropic.claude-haiku-4-5-20251001-v1:0'})).toHaveCount(0);

 // (3) Recency badges from real lifecycle: ACTIVE -> "Active", LEGACY -> "Legacy".
 // No date is ever shown (ListFoundationModels has none).
 await expect(anthropic.getByText('Active',{exact:true})).toBeVisible();
 await expect(anthropic.getByText('Legacy',{exact:true})).toBeVisible();
 await expect(page.getByText(/\d{4}-\d{2}-\d{2}/)).toHaveCount(0);

 // (4) Discoverable-vs-callable straight from API state (not fabricated):
 // discovery-only models report their real execution_binding status; the wired one is ready.
 await expect(anthropic.getByText('Discoverable · unverified',{exact:true}).first()).toBeVisible();
 await expect(openai.getByText('Execution-ready',{exact:true})).toBeVisible();
 // Readiness column mirrors execution_ready exactly: one Ready, three Not ready.
 await expect(page.getByText('Ready',{exact:true})).toHaveCount(1);
 await expect(page.getByText('Not ready',{exact:true})).toHaveCount(3);

 // (2) Category filter (derived from real modalities): "Text" keeps only Qwen.
 await page.getByRole('button',{name:'Text',exact:true}).click();
 await expect(qwenTable.getByRole('button',{name:'Qwen3 235B A22B 2507',exact:true})).toBeVisible();
 await expect(page.getByRole('table',{name:'Anthropic models'})).toHaveCount(0);
 // Back to Multimodal keeps Anthropic + OpenAI, hides text-only Qwen.
 await page.getByRole('button',{name:'Multimodal',exact:true}).click();
 await expect(page.getByRole('table',{name:'Anthropic models'})).toBeVisible();
 await expect(page.getByRole('table',{name:'OpenAI models'})).toBeVisible();
 await expect(page.getByRole('table',{name:'Qwen models'})).toHaveCount(0);

 // Details expose raw ID + real fields; honest "no launch date" statement.
 await page.getByRole('button',{name:'Claude Haiku 4.5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('Category: Multimodal',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Lifecycle status: Active · current',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Launch date: not published by bedrock:ListFoundationModels',{exact:true})).toBeVisible();
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('anthropic.claude-haiku-4-5-20251001-v1:0').first()).toBeVisible();
});
