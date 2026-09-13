import {test,expect} from '@playwright/test';
// Local UI contracts: responses are intercepted, not live authorization evidence.
const cases=[
 {name:'permission 403',status:403,body:JSON.stringify({detail:'Access denied'}),type:'application/json'},
 {name:'gateway 403',status:403,body:JSON.stringify({message:'Forbidden'}),type:'application/json'},
 {name:'unknown JSON 403',status:403,body:JSON.stringify({reason:'Unknown rejection'}),type:'application/json'},
 {name:'HTML 403',status:403,body:'<html>Forbidden</html>',type:'text/html'},
 {name:'invalid JSON 403',status:403,body:'{invalid',type:'application/json'},
 {name:'service 500',status:500,body:'Unavailable',type:'text/plain'},
 {name:'service 503',status:503,body:'Unavailable',type:'text/plain'},
 {name:'network failure',status:0,body:'',type:'text/plain'},
];
for(const c of cases){
 test(`${c.name}: public home survives retry and menu offers explicit login`,async({page})=>{
  let calls=0,loginCalls=0;
  await page.route('**/studio-config.json',r=>r.fulfill({json:{hosted:true,mode:'CLOUD-HOSTED DEMO'}}));
  await page.route('**/api/me',r=>{calls++;return c.status?r.fulfill({status:c.status,body:c.body,contentType:c.type}):r.abort('failed');});
  await page.route('**/auth/login',r=>{loginCalls++;return r.fulfill({contentType:'text/html',body:'<h1>Test authentication endpoint</h1>'});});
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Session unavailable',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Your expertise. Your workspace.',exact:true})).toBeVisible();
  for(const heading of ['Build an agent','Evaluate and improve','Work within policy'])await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible();
  expect(loginCalls).toBe(0);
  await expect(page.getByRole('heading',{name:'My agents',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Retry session check',exact:true}).click();
  await expect.poll(()=>calls).toBeGreaterThan(1);
  await expect(page.getByRole('heading',{name:'Your expertise. Your workspace.',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Session unavailable',exact:true}).click();
  await expect(page.getByRole('menuitem',{name:'Retry session check',exact:true})).toBeVisible();
  await page.getByRole('menuitem',{name:'Sign in again',exact:true}).click();
  await expect(page).toHaveURL(/\/auth\/login$/);
  await expect(page.getByRole('heading',{name:'Test authentication endpoint'})).toBeVisible();
  expect(loginCalls).toBe(1);
 });
}
