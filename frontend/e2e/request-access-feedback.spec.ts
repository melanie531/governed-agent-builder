import {test,expect} from '@playwright/test';

// Task 1: "Request access" submit feedback must be surfaced INSIDE the modal.
// Root cause fixed: on a duplicate request the backend returns HTTP 409
// "A request is already pending", but the old UI rendered the feedback behind
// the request modal, so the user saw nothing happen. These specs assert the
// three submit paths and input preservation on failure using deterministic
// route mocks over a requestable discovery model.

const model={id:'discovery:bedrock:anthropic.claude-opus-5',record_id:'discovery:bedrock:anthropic.claude-opus-5',version:'1',
 kind:'model',name:'Claude Opus 5',provider:'Anthropic',model_id:'anthropic.claude-opus-5',
 description:'Anthropic foundation model discovered via bedrock:ListFoundationModels.',
 capabilities:['input:text','output:text'],data_handling:'Discovery metadata only',policy_reason:'',
 origin:'Bedrock foundation-model discovery',refreshed_at:null,fixture:false,approved:true,external:false,
 status:'requestable',usable:false,granted:false,requestable:true,execution_ready:false};

async function openRequestForm(page){
 await page.route('**/studio-config.json',r=>r.fulfill({json:{hosted:true,mode:'hosted'}}));
 await page.route('**/api/me',r=>r.fulfill({json:{persona:{id:'qa',name:'QA',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic'}}));
 await page.route('**/api/agents',r=>r.fulfill({json:[]}));
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[model],count:1,mode:'live',agent_listing_implemented:true}}));
 await page.goto('/');
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 await page.getByRole('button',{name:'View details',exact:true}).click();
 const dialog=page.getByRole('dialog');
 await dialog.getByRole('button',{name:'Request access',exact:true}).click();
 return dialog;
}

test('new request success surfaces the pending confirmation',async({page})=>{
 const dialog=await openRequestForm(page);
 await page.route('**/api/requests',r=>r.fulfill({status:201,json:{id:'req-new'}}));
 await dialog.getByRole('textbox',{name:'Workspace business purpose'}).fill('Synthetic new-request business purpose');
 await dialog.getByRole('button',{name:'Submit access request',exact:true}).click();
 // Success path: pending confirmation shown; no error surfaced.
 await expect(page.getByText('Access request sent. Pending approval.',{exact:false})).toBeVisible();
});

test('duplicate request 409 shows an in-modal pending message and preserves input',async({page})=>{
 const dialog=await openRequestForm(page);
 await page.route('**/api/requests',r=>r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'A request is already pending'})}));
 const purpose='Synthetic duplicate-request business purpose';
 await dialog.getByRole('textbox',{name:'Workspace business purpose'}).fill(purpose);
 await dialog.getByRole('button',{name:'Submit access request',exact:true}).click();
 // 409 duplicate: clear in-modal message plus a way to view the pending request.
 await expect(dialog.getByText('You already have a pending request',{exact:false})).toBeVisible();
 await expect(dialog.getByRole('button',{name:'View your pending request status',exact:true})).toBeVisible();
 // Not treated as success: no success confirmation.
 await expect(page.getByText('Access request sent. Pending approval.',{exact:false})).toHaveCount(0);
 // Input is preserved on failure.
 await expect(dialog.getByRole('textbox',{name:'Workspace business purpose'})).toHaveValue(purpose);
});

test('generic API error is surfaced in-modal and input is preserved',async({page})=>{
 const dialog=await openRequestForm(page);
 await page.route('**/api/requests',r=>r.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'Server unavailable'})}));
 const purpose='Synthetic error-path business purpose';
 await dialog.getByRole('textbox',{name:'Workspace business purpose'}).fill(purpose);
 await dialog.getByRole('button',{name:'Submit access request',exact:true}).click();
 // Generic failure: real feedback shown, not faked success.
 await expect(dialog.getByText('Request could not be sent',{exact:false})).toBeVisible();
 await expect(page.getByText('Access request sent. Pending approval.',{exact:false})).toHaveCount(0);
 // Input is preserved on failure.
 await expect(dialog.getByRole('textbox',{name:'Workspace business purpose'})).toHaveValue(purpose);
});
