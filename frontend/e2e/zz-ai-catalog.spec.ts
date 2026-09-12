import {test,expect} from '@playwright/test';

test('AI Catalog development fixture request, admin approval and refreshed selection',async({page})=>{
 await page.goto('/');
 await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const navigate=async(name:string)=>{const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();await page.getByRole('link',{name,exact:true}).click();};
 await navigate('AI Catalog');
 await expect(page.getByText('Development fixture catalog only.',{exact:false})).toBeVisible();
 await expect(page.getByText('Native Registry / Model Gateway: NotConnected.',{exact:false})).toBeVisible();
 await page.getByRole('tab',{name:'Tools',exact:true}).click();
 const row=page.getByRole('row').filter({hasText:'Synthetic strategy insights'});
 await row.getByRole('button',{name:'Request access'}).click();
 await page.getByRole('textbox',{name:'Workspace business purpose'}).fill('Synthetic catalog browser acceptance purpose');
 await page.getByRole('button',{name:'Submit access request'}).click();
 await expect(page.getByText('Access request recorded.',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:/DEV ONLY/}).click();
 await page.getByRole('menuitem',{name:/Platform Admin/}).click();
 await navigate('Policies & approvals');
 await page.getByRole('textbox',{name:'Decision reason for restricted-insights'}).fill('Synthetic browser approval for research');
 await page.getByRole('button',{name:'Approve access',exact:true}).click();
 await expect(page.getByText('APPROVED',{exact:true}).last()).toBeVisible();
 await page.getByRole('button',{name:/DEV ONLY/}).click();
 await page.getByRole('menuitem',{name:/Sam Taylor/}).click();
 await navigate('AI Catalog');
 await page.getByRole('tab',{name:'Tools',exact:true}).click();
 await expect(page.getByRole('row').filter({hasText:'Synthetic strategy insights'}).getByRole('button',{name:'Use in builder'})).toBeVisible();
 await page.screenshot({path:'../artifacts/ai-catalog-fixture-evidence.png',fullPage:true});
 // Restore the grant changed by this test; later preserved journeys share this fixture server.
 await page.getByRole('button',{name:/DEV ONLY/}).click();
 await page.getByRole('menuitem',{name:/Platform Admin/}).click();
 await navigate('Tools & skills');
 const grant=page.getByRole('checkbox',{name:'Sam Taylor access to Synthetic strategy insights',exact:true});
 await grant.click();await expect(grant).not.toBeChecked();
});

test('AI Catalog provider unavailable cannot display fixture fallback (synthetic browser adapter)',async({page})=>{
 await page.route('**/api/catalog',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Live catalog unavailable; no fixture fallback.'})}));
 await page.goto('/');await page.getByRole('button',{name:'Enter as Sam Taylor'}).click();
 const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();
 await page.getByRole('link',{name:'AI Catalog',exact:true}).click();
 await expect(page.getByText('Live catalog unavailable; no fixture fallback.',{exact:true})).toBeVisible();
 await expect(page.getByText('Claude · Bedrock',{exact:true})).toHaveCount(0);
});
