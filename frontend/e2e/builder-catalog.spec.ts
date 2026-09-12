import {test,expect} from '@playwright/test';

// Browser UI contract with explicit offline native metadata; backend persistence
// and deploy rejection are independently tested in test_builder_catalog.py.
test('native Catalog returns to same Builder draft preserving prompt and evaluation',async({page})=>{
 const item={id:'model:offline:target:claude',record_id:'model:offline:target:claude',version:'7',kind:'model',name:'Offline native model',description:'Synthetic native descriptor',provider:'Native source',approved:true,external:false,fixture:false,capabilities:[],data_handling:'Synthetic',status:'requestable',policy_reason:'Administrator grant required',requestable:true,usable:false,execution_ready:false,origin:'Offline browser adapter',refreshed_at:null};
 const foundation={id:'research',name:'Research brief',description:'Foundation Library source',icon:'',version:'1.0.0',approved:true,mandatory_defaults:['Current permission checks']};
 const dataset=[{id:'custom-case',input:'Synthetic question',required_terms:[],require_citation:false,expect_refusal:false,expected_format:'text'}];
 await page.route('**/api/build-options*',r=>r.fulfill({json:{foundations:[foundation],choices:{models:[item],tools:[],skills:[]},sample_dataset:dataset,catalog_mode:'live',integration_status:'Exact binding requires platform approval; Runtime NotConnected'}}));
 await page.route('**/api/catalog',r=>r.fulfill({json:{items:[item],count:1,mode:'live',agent_listing_implemented:false}}));
 await page.goto('/');await page.getByRole('button',{name:'Enter as Alex Morgan'}).click();
 await page.getByRole('button',{name:'Create agent',exact:true}).click();
 await page.getByRole('radio',{name:'Select Research brief'}).click();
 await page.getByRole('button',{name:'Next',exact:true}).click();
 // An unresolved model does not prevent writing/saving a source draft.
 await page.getByRole('button',{name:'Next',exact:true}).click();
 await page.getByRole('textbox',{name:/^Agent name/}).fill('Preserved native draft');
 await page.getByRole('textbox',{name:/^Your instructions/}).fill('User prompt survives the catalog round trip.');
 await page.getByRole('textbox',{name:/^Success criteria/}).fill('User evaluation criteria stay unchanged.');
 const nav=async(name:string)=>{const toggle=page.getByRole('button',{name:'Open side navigation',exact:true});if(await toggle.isVisible())await toggle.click();await page.getByRole('link',{name,exact:true}).click();};
 await nav('AI Catalog');
 await page.getByRole('button',{name:'Add to draft (not deployment)',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Create agent',exact:true})).toBeVisible();
 await expect(page.getByText(item.id+' · 7',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Next',exact:true}).click();
 await expect(page.getByRole('textbox',{name:/^Your instructions/})).toHaveValue('User prompt survives the catalog round trip.');
 await expect(page.getByRole('textbox',{name:/^Success criteria/})).toHaveValue('User evaluation criteria stay unchanged.');
 await expect(page.getByRole('textbox',{name:/^Evaluation dataset/})).toHaveValue(/custom-case/);
 await page.screenshot({path:'../artifacts/builder-catalog-draft-preserved.png',fullPage:true});
 await page.getByRole('button',{name:'Next',exact:true}).click();
 await expect(page.getByRole('button',{name:'Save draft',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Deploy & test locally',exact:true})).toHaveCount(0);
});
