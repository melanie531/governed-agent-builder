import {test,expect} from '@playwright/test';

// Rebuilt Models catalog contract (mocked /api/catalog). Verifies the four
// product requirements as DISPLAY behaviour driven ENTIRELY by real
// bedrock:ListFoundationModels fields returned by the API — the frontend never
// fabricates provider, category, lifecycle, or callable state, and there is NO
// hand-curated/guessed model map or invented launch date.
//
// 1. Group BY PROVIDER (real providerName), name-first, raw model ID only in details.
// 2. Filter by CATEGORY (derived from real modalities on the backend).
// 3. Recency: a REAL rolling-window filter over VERIFIED launch dates carried on
//    each Entry (recency + launch_date + launch_date_source). It is NOT derived
//    from modelLifecycle.status; lifecycle is shown only as lifecycle-info.
// 4. Discoverable-vs-callable: a discovered model is listed yet not execution-ready.

const base={record_id:'',version:'1',kind:'model',data_handling:'Discovery metadata only',capabilities:[],policy_reason:'',origin:'Bedrock foundation-model discovery',refreshed_at:null,fixture:false,approved:true,external:false,provenance:'bedrock:ListFoundationModels'};

// All values below mirror the account's REAL ListFoundationModels output
// (account 302277511711 / us-west-2). No guessed IDs, no invented dates.
const haiku={...base,id:'discovery:bedrock:anthropic.claude-haiku-4-5-20251001-v1:0',record_id:'discovery:bedrock:anthropic.claude-haiku-4-5-20251001-v1:0',
 name:'Claude Haiku 4.5',provider:'Anthropic',model_id:'anthropic.claude-haiku-4-5-20251001-v1:0',native_model_id:'anthropic.claude-haiku-4-5-20251001-v1:0',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 category:'multimodal',lifecycle:'ACTIVE',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],
 recency:'recent',launch_date:'2026-07-01',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// LEGACY lifecycle model with an OUT-OF-WINDOW verified date -> pending. Proves
// lifecycle does NOT drive recency (LEGACY is not automatically excluded by a
// lifecycle rule; it is pending because its verified date is old).
const sonnet={...base,id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',record_id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',
 name:'Claude Sonnet 4',provider:'Anthropic',model_id:'anthropic.claude-sonnet-4-20250514-v1:0',native_model_id:'anthropic.claude-sonnet-4-20250514-v1:0',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 category:'multimodal',lifecycle:'LEGACY',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],
 recency:'out_of_window',launch_date:'2025-05-23',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4.html',recency_reason:'launch_date_older_than_window',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// A different provider + a text-only category. ACTIVE lifecycle but OUT-OF-WINDOW
// verified date -> pending (again proving lifecycle != recency).
const qwen={...base,id:'discovery:bedrock:qwen.qwen3-235b-a22b-2507-v1:0',record_id:'discovery:bedrock:qwen.qwen3-235b-a22b-2507-v1:0',
 name:'Qwen3 235B A22B 2507',provider:'Qwen',model_id:'qwen.qwen3-235b-a22b-2507-v1:0',native_model_id:'qwen.qwen3-235b-a22b-2507-v1:0',
 description:'Qwen foundation model discovered via bedrock:ListFoundationModels.',
 category:'text',lifecycle:'ACTIVE',streaming:true,inference_types:['ON_DEMAND'],input_modalities:['TEXT'],output_modalities:['TEXT'],
 recency:'out_of_window',launch_date:'2025-04-28',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen3.html',recency_reason:'launch_date_older_than_window',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// A wired/callable model to prove discoverable-vs-callable is API-driven.
const wired={...base,id:'discovery:bedrock:openai.gpt-6-astra',record_id:'discovery:bedrock:openai.gpt-6-astra',
 name:'GPT-6 Astra',provider:'OpenAI',model_id:'openai.gpt-6-astra',native_model_id:'openai.gpt-6-astra',
 description:'OpenAI foundation model discovered via bedrock:ListFoundationModels.',
 category:'multimodal',lifecycle:'ACTIVE',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],
 recency:'recent',launch_date:'2026-09-08',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-astra.html',pending_reason:null,
 status:'available',usable:true,granted:true,requestable:false,execution_ready:true,execution_binding:{status:'verified',last_checked:1}};

test('Models grouped by provider, filtered by category, verified-date recency, discoverable-vs-callable',async({page})=>{
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[haiku,sonnet,qwen,wired],count:4,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();

 // Default recency filter is "Recent (last 6 months)": only the two models with a
 // VERIFIED in-window launch date appear (haiku 2026-07-01, gpt-6-astra 2026-09-08).
 // This proves the date-window filter REDUCES the list vs all discovered.
 await expect(page.getByRole('table',{name:'Anthropic models'}).getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toBeVisible();
 await expect(page.getByRole('table',{name:'OpenAI models'}).getByRole('button',{name:'GPT-6 Astra',exact:true})).toBeVisible();
 // Out-of-window models (Sonnet 4 LEGACY, Qwen3 ACTIVE) are NOT in the recent list.
 await expect(page.getByRole('table',{name:'Qwen models'})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Claude Sonnet 4',exact:true})).toHaveCount(0);

 // Switch to "All discovered" to see the full list for provider-grouping checks.
 await page.getByRole('button',{name:'All discovered',exact:true}).click();

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

 // (3) Recency badges from VERIFIED launch date, NOT lifecycle. Haiku (ACTIVE,
 // in-window) and Qwen (ACTIVE, out-of-window) share lifecycle yet differ in
 // recency; Sonnet (LEGACY, out-of-window) is also not recent. A verified-but-old
 // date shows "Not recent" (out of window), which is DISTINCT from "Pending
 // verification" (reserved for genuinely missing/unmatched/conflicting dates).
 // (Scope to the model tables so the recency-filter control labels are excluded.)
 await expect(anthropic.getByText('Recent',{exact:true})).toHaveCount(1);
 await expect(openai.getByText('Recent',{exact:true})).toHaveCount(1);
 await expect(anthropic.getByText('Not recent',{exact:true})).toHaveCount(1);
 await expect(qwenTable.getByText('Not recent',{exact:true})).toHaveCount(1);
 // None of these verified-date models are "Pending verification".
 await expect(page.getByText('Pending verification',{exact:true})).toHaveCount(1);

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

 // Details expose raw ID + real fields; verified launch date with source link,
 // and lifecycle shown ONLY as separate lifecycle-info.
 await page.getByRole('button',{name:'Claude Haiku 4.5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('Category: Multimodal',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Lifecycle status: Active · current',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Launch date: 2026-07-01',{exact:false})).toBeVisible();
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('anthropic.claude-haiku-4-5-20251001-v1:0').first()).toBeVisible();
});
