import {test,expect} from '@playwright/test';

// Models catalog presentation contract AFTER removal of the user-facing Recent
// badge and the All-discovered/Recent recency filter control.
//
// The 6-month recency window remains a BACKEND-only classification: the backend
// (backend/model_recency.py) still performs the EXACT-modelId join against
// reviewer-verified launch dates and returns only recent-window models by
// default. This spec fixes the API payload to the recent-window set the backend
// would return and asserts the UI:
//   - keeps provider grouping (Anthropic / OpenAI tables),
//   - renders the concrete account models Melanie named,
//   - discloses the verified launch date (with source link) in model details,
//   - shows NO "Recent" badge text and NO recency filter control.
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

async function openCatalog(page){
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[opus5,sonnet5,gpt6],count:3,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 await expect(page.getByRole('button',{name:'Sam Taylor · Operations desk',exact:true})).toBeVisible();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
}

test('backend-recent models render per provider without any user-facing Recent badge or recency filter',async({page})=>{
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

 // Recent badge text and the recency filter control are removed from the UI.
 await expect(page.getByText('Recent',{exact:true})).toHaveCount(0);
 await expect(page.getByText('Not recent',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Recent (last 6 months)',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'All discovered',exact:true})).toHaveCount(0);
 await expect(page.getByText('Filter models by recency',{exact:false})).toHaveCount(0);

 // Details still expose the verified launch date with its source link.
 await page.getByRole('button',{name:'Claude Opus 5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByText('Verified launch date: 2026-07-24',{exact:false})).toBeVisible();
 await expect(dialog.getByRole('link',{name:'source',exact:true})).toBeVisible();
});
