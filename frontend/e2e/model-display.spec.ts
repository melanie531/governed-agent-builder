import {test,expect} from '@playwright/test';

// Presentation-only contract for the AI Catalog Models list (mocked /api/catalog).
// The Models list is driven by REAL bedrock:ListFoundationModels fields carried
// on each Entry: the friendly name is the real modelName, the provider is the
// real providerName, and models group BY PROVIDER (Bedrock-catalog style). The
// raw model ID appears only inside View details. There is no curated/guessed
// relabelling map. Access/readiness/approval semantics come from the API only.
const base={record_id:'',version:'1',kind:'model',description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',provider:'Anthropic',data_handling:'Discovery metadata only',capabilities:[],status:'blocked',policy_reason:'',requestable:false,usable:false,origin:'Bedrock foundation-model discovery',refreshed_at:null,fixture:false,approved:true,external:false,execution_ready:false,category:'multimodal',lifecycle:'ACTIVE',streaming:true,inference_types:['INFERENCE_PROFILE'],input_modalities:['TEXT','IMAGE'],output_modalities:['TEXT'],execution_binding:{status:'unverified',last_checked:null},recency:'recent',launch_date:'2026-07-01',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card.html',pending_reason:null};
const haiku={...base,id:'discovery:bedrock:anthropic.claude-haiku-4-5-20251001-v1:0',record_id:'discovery:bedrock:anthropic.claude-haiku-4-5-20251001-v1:0',name:'Claude Haiku 4.5',model_id:'anthropic.claude-haiku-4-5-20251001-v1:0',native_model_id:'anthropic.claude-haiku-4-5-20251001-v1:0'};
const legacy={...base,id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',record_id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',name:'Claude Sonnet 4',model_id:'anthropic.claude-sonnet-4-20250514-v1:0',native_model_id:'anthropic.claude-sonnet-4-20250514-v1:0',lifecycle:'LEGACY',requestable:false,usable:false,launch_date:'2026-08-01'};

test('Models list shows real modelName + providerName; raw model ID only in View details',async({page})=>{
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[haiku,legacy],count:2,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 // List: real modelName shown, not the raw model ID.
 await expect(page.getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Claude Sonnet 4',exact:true})).toBeVisible();
 // Provider grouping: an Anthropic-labelled table groups both real Anthropic models.
 const anthropic=page.getByRole('table',{name:'Anthropic models'});
 await expect(anthropic).toBeVisible();
 // Raw model ID is not shown as a title in the list.
 await expect(page.getByRole('button',{name:'anthropic.claude-haiku-4-5-20251001-v1:0'})).toHaveCount(0);
 // Readiness markers from the API (both discovery-only, not callable).
 await expect(page.getByText('Not ready',{exact:true})).toHaveCount(2);
 // View details: raw model ID present only here.
 await page.getByRole('button',{name:'Claude Haiku 4.5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByRole('heading',{name:'Claude Haiku 4.5'})).toBeVisible();
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('anthropic.claude-haiku-4-5-20251001-v1:0').first()).toBeVisible();
 await expect(dialog.getByText('Provider: Anthropic',{exact:true})).toBeVisible();
});
