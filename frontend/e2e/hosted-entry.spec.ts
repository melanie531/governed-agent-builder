import {test,expect} from '@playwright/test';

// UI-only contract: intercepted API responses are explicitly not hosted E2E or
// evidence of Cognito authentication. Backend auth is tested separately.
test('hosted Agent Studio entry never displays persona selection',async({page})=>{
 await page.route('**/studio-config.json',route=>route.fulfill({json:{hosted:true,mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/api/me',route=>route.fulfill({status:401,json:{detail:'Sign in to Agent Studio'}}));
 const demoCalls:string[]=[];page.on('request',r=>{if(r.url().includes('/api/demo/'))demoCalls.push(r.url());});
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Agent Studio',exact:true})).toBeVisible();
 await expect(page.getByRole('link',{name:'Sign in / Open Studio'})).toHaveAttribute('href','/auth/login');
 await expect(page.getByText('Enter as Alex Morgan')).toHaveCount(0);
 await expect(page.getByText('Choose demo identity · DEV ONLY')).toHaveCount(0);
 await expect(page.getByText('Platform Admin')).toHaveCount(0);
 await page.screenshot({path:'../artifacts/agent-studio-entry.png',fullPage:true});
 expect(demoCalls).toEqual([]);
});

test('hosted current-user menu offers logout, never a role switch',async({page})=>{
 await page.route('**/studio-config.json',route=>route.fulfill({json:{hosted:true,mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/api/me',route=>route.fulfill({json:{persona:{id:'synthetic-member',name:'member@example.test',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic-test-csrf',mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/api/agents',route=>route.fulfill({json:[]}));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'member@example.test · Research studio'}).click();
 await expect(page.getByRole('menuitem',{name:'Sign out'})).toBeVisible();
 await expect(page.getByRole('menuitem',{name:/Alex|Sam|Admin/})).toHaveCount(0);
 await expect(page.getByText('Policies & approvals',{exact:true})).toHaveCount(0);
});

test('mocked pending verification sends and completes before workspace',async({page})=>{
 let verified=false;
 const pending={state:'PENDING_EMAIL_VERIFICATION',csrf:'offline-csrf',expires:Math.floor(Date.now()/1000)+600,next_send:0,sends_remaining:3};
 await page.route('**/studio-config.json',r=>r.fulfill({json:{hosted:true,mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/api/me',r=>r.fulfill(verified?{json:{persona:{id:'offline',name:'Member',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'business-csrf'}}:{status:401,json:{detail:'Unauthorized'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill({json:pending}));
 await page.route('**/auth/verification/send',r=>{expect(r.request().headers()['x-csrf-token']).toBe('offline-csrf');return r.fulfill({json:{...pending,sent:true,sends_remaining:2,next_send:Math.floor(Date.now()/1000)+60}});});
 await page.route('**/auth/verification/verify',r=>{expect(r.request().postDataJSON()).toEqual({code:'123456'});verified=true;return r.fulfill({json:{verified:true}});});
 await page.route('**/api/agents',r=>r.fulfill({json:[]}));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Verify email',exact:true})).toBeVisible();
 await page.evaluate(()=>window.dispatchEvent(new Event('studio-session-expired')));
 await expect(page.getByRole('heading',{name:'Verify email',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Send verification code',exact:true}).click();
 await expect(page.getByText('Verification code sent. Check your email.')).toBeVisible();
 await expect(page.getByRole('button',{name:'Resend verification code',exact:true})).toBeDisabled();
 await page.getByRole('textbox',{name:'Verification code',exact:true}).fill('123456');
 await page.getByRole('button',{name:'Verify email and open Studio'}).click();
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 expect(page.url()).not.toContain('123456');
 expect(await page.evaluate(()=>Object.values(localStorage))).not.toContain('123456');
});

test('mocked send failure never claims delivery and expiry supports restart',async({page})=>{
 const pending={state:'PENDING_EMAIL_VERIFICATION',csrf:'offline-csrf',expires:Math.floor(Date.now()/1000)+3,next_send:0,sends_remaining:3};
 await page.route('**/studio-config.json',r=>r.fulfill({json:{hosted:true,mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/api/me',r=>r.fulfill({status:401,json:{detail:'Unauthorized'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill({json:pending}));
 await page.route('**/auth/verification/send',r=>r.fulfill({status:400,json:{detail:'Verification could not be completed: LimitExceededException'}}));
 await page.goto('/');
 await page.getByRole('button',{name:'Send verification code',exact:true}).click();
 await expect(page.getByText(/LimitExceededException/)).toBeVisible();
 await expect(page.getByText('Verification code sent. Check your email.')).toHaveCount(0);
 await expect(page.getByText('Verification expired. Start sign-in again.')).toBeVisible();
 await expect(page.getByRole('link',{name:'Start sign-in again'})).toHaveAttribute('href','/auth/login');
 await expect(page.getByRole('textbox',{name:'Verification code',exact:true})).toBeDisabled();
});
