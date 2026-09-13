import {test,expect} from '@playwright/test';

// UI-only contract (mocked HTTP, not hosted E2E evidence):
// 401 = not signed in -> anonymous sign-in page.
// 403 = access denied for the presented session/identity -> denied/error state
//       with an explicit user-driven "Sign in" entry plus retry. Never
//       silently treated as signed-out, never auto-redirected.
// 5xx = service unavailable -> error state with retry and sign-in entry.
const hostedConfig={json:{hosted:true,mode:'CLOUD-HOSTED DEMO'}};
const noVerification={status:401,json:{detail:'No pending verification'}};

for(const [label,me] of Object.entries({
 'JSON without detail':{status:403,contentType:'application/json',body:JSON.stringify({message:'Forbidden'})},
 'HTML body':{status:403,contentType:'text/html',body:'<html><body>Forbidden</body></html>'},
 'invalid JSON body':{status:403,contentType:'application/json',body:'{forbidden'},
})){
 test(`session-check 403 (${label}) is access denied with sign-in entry, not signed-out, no auto-redirect`,async({page})=>{
  await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
  await page.route('**/api/me',r=>r.fulfill(me));
  await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
  await page.goto('/');
  // Denied state retains public content, with explicit recovery and no auto navigation.
  await expect(page.getByRole('heading',{name:'Your expertise. Your workspace.',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Retry session check'})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Sign in / Open Studio'})).toHaveCount(0);
  await expect(page.getByText('Access was denied. Sign in or retry the session check.')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/');
 });
}

test('session-check 401 stays the anonymous sign-in page',async({page})=>{
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill({status:401,json:{message:'Unauthorized'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
 await page.goto('/');
 await expect(page.getByRole('button',{name:'Sign in / Open Studio'})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Sign in',exact:true})).toHaveCount(0);
});

test('session-check 5xx is service unavailable with retry and sign-in entry',async({page})=>{
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill({status:503,json:{message:'Service Unavailable'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
 await page.goto('/');
 await expect(page.getByText('Studio is unavailable. Try again.')).toBeVisible();
 await expect(page.getByRole('button',{name:'Retry session check'})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Sign in',exact:true})).toBeEnabled();
 expect(new URL(page.url()).pathname).toBe('/');
});

test('Session unavailable menu offers Sign in and retry; sign-in uses server /auth/login',async({page})=>{
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill({status:403,json:{message:'Forbidden'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
 await page.route('**/auth/login',r=>r.fulfill({contentType:'text/html',body:'<html><body>server-login-flow</body></html>'}));
 await page.goto('/');
 await page.getByRole('button',{name:'Session unavailable'}).click();
 await expect(page.getByRole('menuitem',{name:'Retry session check'})).toBeVisible();
 await page.getByRole('menuitem',{name:'Sign in',exact:true}).click();
 await page.waitForURL('**/auth/login');
 expect(new URL(page.url()).pathname).toBe('/auth/login');
});

test('error-page Sign in button navigates to server /auth/login only on click',async({page})=>{
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill({status:403,json:{message:'Forbidden'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
 await page.route('**/auth/login',r=>r.fulfill({contentType:'text/html',body:'<html><body>server-login-flow</body></html>'}));
 await page.goto('/');
 const signIn=page.getByRole('button',{name:'Sign in',exact:true});
 await expect(signIn).toBeEnabled();
 expect(new URL(page.url()).pathname).toBe('/'); // no auto redirect
 await signIn.click();
 await page.waitForURL('**/auth/login');
 expect(new URL(page.url()).pathname).toBe('/auth/login');
});

test('mid-session gateway 403 surfaces access denied, never auto-signs-out or redirects',async({page})=>{
 let expired=false;
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill({json:{persona:{id:'synthetic-member',name:'member@example.test',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic-test-csrf',mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
 await page.route('**/api/agents',r=>r.fulfill({json:[]}));
 await page.route('**/api/capabilities',r=>r.fulfill(expired?{status:403,json:{message:'Forbidden'}}:{json:[]}));
 await page.route('**/api/requests',r=>r.fulfill(expired?{status:403,json:{message:'Forbidden'}}:{json:[]}));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 expired=true;
 await page.getByRole('link',{name:'Capability requests'}).click();
 await expect(page.getByRole('group',{name:'Action blocked Access is restricted. Request access or contact your administrator.',exact:true})).toBeVisible();
 // Still signed in; user decides how to recover. No auto sign-out or redirect.
 await expect(page.getByRole('button',{name:'member@example.test · Research studio'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Sign in / Open Studio'})).toHaveCount(0);
 expect(new URL(page.url()).pathname).toBe('/');
});

test('mid-session 401 expires the session to the anonymous entry',async({page})=>{
 let expired=false;
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill(expired?{status:401,json:{message:'Unauthorized'}}:{json:{persona:{id:'synthetic-member',name:'member@example.test',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic-test-csrf',mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/auth/verification/status',r=>r.fulfill(noVerification));
 await page.route('**/api/agents',r=>r.fulfill(expired?{status:401,json:{message:'Unauthorized'}}:{json:[]}));
 await page.route('**/api/capabilities',r=>r.fulfill(expired?{status:401,json:{message:'Unauthorized'}}:{json:[]}));
 await page.route('**/api/requests',r=>r.fulfill(expired?{status:401,json:{message:'Unauthorized'}}:{json:[]}));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 expired=true;
 await page.getByRole('link',{name:'Capability requests'}).click();
 await expect(page.getByRole('button',{name:'Sign in / Open Studio'})).toBeEnabled();
});

test('application 403 with business detail is surfaced verbatim semantics, never treated as sign-out',async({page})=>{
 await page.route('**/studio-config.json',r=>r.fulfill(hostedConfig));
 await page.route('**/api/me',r=>r.fulfill({json:{persona:{id:'synthetic-member',name:'member@example.test',role:'business',workspace:'research',workspace_name:'Research studio'},csrf:'synthetic-test-csrf',mode:'CLOUD-HOSTED DEMO'}}));
 await page.route('**/api/agents',r=>r.fulfill({json:[]}));
 await page.route('**/api/capabilities',r=>r.fulfill({status:403,json:{detail:'Business identity required'}}));
 await page.route('**/api/requests',r=>r.fulfill({status:403,json:{detail:'Business identity required'}}));
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'My agents',exact:true})).toBeVisible();
 await page.getByRole('link',{name:'Capability requests'}).click();
 await expect(page.getByText('Business identity required')).toBeVisible();
 await expect(page.getByRole('button',{name:'member@example.test · Research studio'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Sign in / Open Studio'})).toHaveCount(0);
});
