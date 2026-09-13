import {test,expect} from '@playwright/test';

// ADDITIVE recency-layer contract for the integrated Models catalog.
// This spec exercises the recency layer LAYERED ON TOP of the existing
// provider-grouped Models presentation (see models-presentation.spec.ts and
// model-directory.spec.ts for the base provider-grouping / display behaviour).
//
// The recency layer is a REAL rolling-window classification over VERIFIED launch
// dates carried on each Entry (recency + launch_date + launch_date_source),
// computed server-side by an EXACT-modelId join against reviewer-verified
// official model cards (backend/model_recency.py). It is NOT derived from
// modelLifecycle.status. Out-of-window = verified but older than 6 months;
// pending = genuinely unverifiable (missing/unmatched/conflicting date).
//
// All field values below mirror the account's REAL ListFoundationModels output
// and the reviewer-verified launch-date evidence. No guessed IDs, no invented
// dates: Opus 5 / Sonnet 5 / GPT-6 Astra are the real account models Melanie
// named, with their verified in-window launch dates.

const base={record_id:'',version:'1',kind:'model',data_handling:'Discovery metadata only',capabilities:['input:text','output:text'],policy_reason:'',origin:'Bedrock foundation-model discovery',refreshed_at:null,fixture:false,approved:true,external:false,provenance:'bedrock:ListFoundationModels'};

const opus5={...base,id:'discovery:bedrock:anthropic.claude-opus-5',record_id:'discovery:bedrock:anthropic.claude-opus-5',
 name:'Claude Opus 5',provider:'Anthropic',model_id:'anthropic.claude-opus-5',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 recency:'recent',launch_date:'2026-07-24',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5.html',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
const sonnet5={...base,id:'discovery:bedrock:anthropic.claude-sonnet-5',record_id:'discovery:bedrock:anthropic.claude-sonnet-5',
 name:'Claude Sonnet 5',provider:'Anthropic',model_id:'anthropic.claude-sonnet-5',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 recency:'recent',launch_date:'2026-06-30',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
const gpt6={...base,id:'discovery:bedrock:openai.gpt-6-astra',record_id:'discovery:bedrock:openai.gpt-6-astra',
 name:'GPT-6 Astra',provider:'OpenAI',model_id:'openai.gpt-6-astra',
 description:'OpenAI foundation model discovered via bedrock:ListFoundationModels.',
 recency:'recent',launch_date:'2026-09-08',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-astra.html',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};
// Verified but OUT-OF-WINDOW date -> "Not recent" (distinct from pending).
const oldModel={...base,id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',record_id:'discovery:bedrock:anthropic.claude-sonnet-4-20250514-v1:0',
 name:'Claude Sonnet 4',provider:'Anthropic',model_id:'anthropic.claude-sonnet-4-20250514-v1:0',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 recency:'out_of_window',launch_date:'2025-05-23',launch_date_source:'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4.html',recency_reason:'launch_date_older_than_window',pending_reason:null,
 status:'blocked',usable:false,granted:false,requestable:false,execution_ready:false,execution_binding:{status:'unverified',last_checked:null}};

async function openCatalog(page){
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[opus5,sonnet5,gpt6,oldModel],count:4,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
}

test('verified-date recency: named in-window models render per provider; recency filter reduces the list',async({page})=>{
 await openCatalog(page);

 // Provider grouping (base behaviour, preserved): Anthropic and OpenAI tables.
 const anthropic=page.getByRole('table',{name:'Anthropic'});
 const openai=page.getByRole('table',{name:'OpenAI'});
 await expect(anthropic).toBeVisible();
 await expect(openai).toBeVisible();

 // The concrete in-window account models Melanie named render under their provider.
 await expect(anthropic.getByRole('button',{name:'Claude Opus 5',exact:true})).toBeVisible();
 await expect(anthropic.getByRole('button',{name:'Claude Sonnet 5',exact:true})).toBeVisible();
 await expect(openai.getByRole('button',{name:'GPT-6 Astra',exact:true})).toBeVisible();

 // Recency badges from VERIFIED launch dates: three Recent (in-window), one Not
 // recent (verified but old). Distinct from Pending verification (none here).
 // Scope to the provider tables so the recency-filter control labels (e.g. the
 // "Not recent" segmented-control option) are excluded from the badge count.
 await expect(anthropic.getByText('Recent',{exact:true})).toHaveCount(2);
 await expect(openai.getByText('Recent',{exact:true})).toHaveCount(1);
 await expect(anthropic.getByText('Not recent',{exact:true})).toHaveCount(1);
 await expect(openai.getByText('Not recent',{exact:true})).toHaveCount(0);

 // The recency filter is a REAL window filter: "Recent (last 6 months)" drops
 // the out-of-window model, keeping only the three verified in-window models.
 await page.getByRole('button',{name:'Recent (last 6 months)',exact:true}).click();
 await expect(anthropic.getByRole('button',{name:'Claude Opus 5',exact:true})).toBeVisible();
 await expect(anthropic.getByRole('button',{name:'Claude Sonnet 5',exact:true})).toBeVisible();
 await expect(openai.getByRole('button',{name:'GPT-6 Astra',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Claude Sonnet 4',exact:true})).toHaveCount(0);

 // Details expose the verified launch date with its source link.
 await page.getByRole('button',{name:'Claude Opus 5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('Verified launch date: 2026-07-24',{exact:false})).toBeVisible();
 await expect(dialog.getByRole('link',{name:'source',exact:true})).toBeVisible();
});
