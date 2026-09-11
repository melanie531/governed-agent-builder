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
