import {test,expect} from '@playwright/test';

// Models presentation first batch (mocked /api/catalog with the REAL sanitized
// live payload field structure captured 2026-09-13 from deployment ff6d73b):
// - the live payload has NO native_model_id; friendly naming must still work
//   via the prefix-stripped model_id path, and only for the exact verified ID.
// - status==='requestable' renders as "Available to request" in the Access
//   column for models; View details keeps the Request access button.
// - "Not ready" is explained from actual fields: granted===false -> Access
//   required (with the real policy_reason), execution_binding.status ===
//   'unverified' / execution_ready===false -> Execution not verified; missing
//   fields are reported as unknown, never invented.
// - Version hash and Runtime route live inside Technical identifiers, not in
//   the business metadata block. Full IDs stay in the collapsed section.
// - Unknown model IDs are never relabelled as Haiku and keep their original
//   description and identifiers. usable/granted/binding semantics untouched.

// Field structure mirrors catalog_sanitized.json (ids are synthetic placeholders).
const haiku={
 id:'model:demo-gateway-record:tgt0000001:us.anthropic.claude-haiku-4-5-20251001-v1:0',
 record_id:'model:demo-gateway-record:tgt0000001:us.anthropic.claude-haiku-4-5-20251001-v1:0',
 name:'us.anthropic.claude-haiku-4-5-20251001-v1:0',
 version:'0efbe171d23945bcbf817b1620fdcbd45ec1a5cd0456890036eae214cea03cb8',
 kind:'model',catalog:'journey',provider:'Amazon Bedrock',
 description:'Approved Runtime route; execution authorization checked separately',
 capabilities:[],
 data_handling:'Existing approved Runtime Haiku route; non-streaming, 16-token validation limit. Execution readiness separately enforced.',
 origin:'AgentCore Model Gateway',refreshed_at:1789288091.2197351,fixture:false,owner:'Platform',
 protocol:'messages',supported:true,
 source_version:'0efbe171d23945bcbf817b1620fdcbd45ec1a5cd0456890036eae214cea03cb8',
 source_revision:'0efbe171d23945bcbf817b1620fdcbd45ec1a5cd0456890036eae214cea03cb8',
 execution_ready:false,execution_binding:{status:'unverified',last_checked:null},
 model_id:'us.anthropic.claude-haiku-4-5-20251001-v1:0',
 target_id:'tgt0000001',connector:'bedrock-runtime passthrough',
 approved:true,external:false,discoverable:true,
 usable:false,granted:false,requestable:true,status:'requestable',
 data_policy_allowed:true,policy_reason:'Administrator grant required'
};
// Same wire shape, unverified model ID, and UNKNOWN readiness facts:
// granted / execution_ready / execution_binding are absent from the payload.
const unknown={
 ...haiku,
 id:'model:demo-gateway-record:tgt0000002:us.anthropic.claude-mystery-9-9-v1:0',
 record_id:'model:demo-gateway-record:tgt0000002:us.anthropic.claude-mystery-9-9-v1:0',
 name:'us.anthropic.claude-mystery-9-9-v1:0',
 model_id:'us.anthropic.claude-mystery-9-9-v1:0',
 target_id:'tgt0000002',
 description:'Enumerated route pending review'
};
delete (unknown as Record<string,unknown>).granted;
delete (unknown as Record<string,unknown>).execution_ready;
delete (unknown as Record<string,unknown>).execution_binding;
delete (unknown as Record<string,unknown>).connector;

async function openCatalog(page){
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[haiku,unknown],count:2,mode:'live',agent_listing_implemented:true}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
}

test('verified Haiku (no native_model_id): neutral description, Available to request, IDs stay collapsed',async({page})=>{
 await openCatalog(page);
 const table=page.getByRole('table');
 // Friendly name still resolves without native_model_id; original name/id untouched elsewhere.
 await expect(page.getByRole('button',{name:'Claude Haiku 4.5',exact:true})).toBeVisible();
 // Neutral purpose description replaces the engineering route copy in the list.
 await expect(table.getByText('Approved Runtime route; execution authorization checked separately',{exact:true})).toHaveCount(0);
 await expect(table.getByText(/Anthropic language model/).first()).toBeVisible();
 // requestable renders as Available to request (English), no raw state word.
 await expect(table.getByText('Available to request',{exact:true})).toHaveCount(2);
 await expect(table.getByText('requestable',{exact:true})).toHaveCount(0);
 // Readiness column stays Not ready (semantics unchanged).
 await expect(table.getByText('Not ready',{exact:true})).toHaveCount(2);
 // Raw route ID is not shown in the table.
 await expect(table.getByText('us.anthropic.claude-haiku-4-5-20251001-v1:0',{exact:true})).toHaveCount(0);
});

test('Haiku details: Not ready explained by real fields; Version hash and Runtime route in Technical identifiers',async({page})=>{
 await openCatalog(page);
 await page.getByRole('button',{name:'Claude Haiku 4.5',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByRole('heading',{name:'Claude Haiku 4.5'})).toBeVisible();
 // Neutral description in the modal body; engineering copy removed from business area.
 await expect(dialog.getByText(/Anthropic language model/).first()).toBeVisible();
 // Not ready is explained by the actual payload fields, separately:
 await expect(dialog.getByText('Access required — Administrator grant required',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Execution not verified',{exact:true})).toBeVisible();
 // Request access action retained.
 await expect(dialog.getByRole('button',{name:'Request access'})).toBeVisible();
 // Version hash / Runtime route are NOT in the business metadata block…
 await expect(dialog.getByText(/^Version: /)).toHaveCount(0);
 // …they live inside the collapsed Technical identifiers section.
 await dialog.getByRole('button',{name:'Technical identifiers'}).click();
 await expect(dialog.getByText('Version hash: 0efbe171d23945bcbf817b1620fdcbd45ec1a5cd0456890036eae214cea03cb8',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Runtime route: bedrock-runtime passthrough',{exact:true})).toBeVisible();
 // Full original IDs preserved, collapsed, unchanged.
 await expect(dialog.getByText('Model / profile ID: us.anthropic.claude-haiku-4-5-20251001-v1:0',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Catalog record: model:demo-gateway-record:tgt0000001:us.anthropic.claude-haiku-4-5-20251001-v1:0',{exact:true})).toBeVisible();
 // Provider is the verified vendor.
 await expect(dialog.getByText('Provider: Anthropic',{exact:true})).toBeVisible();
});

test('unknown model ID is never relabelled as Haiku and missing readiness facts read as unknown',async({page})=>{
 await openCatalog(page);
 // Raw identity kept in the list; not presented as Haiku or Anthropic-verified.
 await expect(page.getByRole('button',{name:'us.anthropic.claude-mystery-9-9-v1:0'})).toBeVisible();
 // Its original description is untouched.
 await expect(page.getByRole('table').getByText('Enumerated route pending review',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'us.anthropic.claude-mystery-9-9-v1:0'}).click();
 const dialog=page.getByRole('dialog');
 await expect(dialog.getByRole('heading',{name:'us.anthropic.claude-mystery-9-9-v1:0'})).toBeVisible();
 // Absent granted / execution fields are reported as unknown, not invented.
 await expect(dialog.getByText('Access status unknown',{exact:true})).toBeVisible();
 await expect(dialog.getByText('Execution status unknown',{exact:true})).toBeVisible();
 // No fabricated verified-vendor metadata.
 await expect(dialog.getByText('Provider: Amazon Bedrock',{exact:true})).toBeVisible();
});
