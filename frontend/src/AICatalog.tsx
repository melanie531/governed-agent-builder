import {actionText, stateText} from './ProductText';
import {CATEGORY_LABELS, ModelCategory, categoryLabel, lifecycleLabel} from './ModelCatalogMeta';
import React, {useEffect, useState} from 'react';
import {Alert, Badge, Box, Button, Container, ExpandableSection, FormField, Header, Input, Modal, SegmentedControl, SpaceBetween, StatusIndicator, Table, Tabs, Textarea} from '@cloudscape-design/components';
export type Entry={id:string;record_id:string;version:string;kind:string;name:string;description:string;provider:string;data_handling:string;capabilities:string[];status:string;policy_reason:string;requestable:boolean;usable:boolean;origin:string;refreshed_at:number|null;fixture:boolean;approved:boolean;external:boolean;owner?:string;provenance?:string;metadata_expires_at?:number;gateway_enumeration?:string;protocol?:string;parent_id?:string;parent_name?:string;operation?:string;descriptor_version?:string;source_revision?:string;inputSchema?:{properties?:Record<string,{type?:string;description?:string}>;required?:string[]};outputSchema?:unknown;supported?:boolean;execution_ready?:boolean;execution_binding?:{status:string;last_checked:number|null};model_id?:string;native_model_id?:string;category?:string;lifecycle?:string|null;streaming?:boolean;inference_types?:string[];input_modalities?:string[];output_modalities?:string[]};
// Presentation only. All model display facts (friendly name, provider, category,
// lifecycle, modalities, raw model ID) come straight from the real
// bedrock:ListFoundationModels fields carried on each Entry — never a curated or
// guessed model map. Access/approval/readiness semantics are unchanged.
const isModel=(x:Entry)=>x.kind==='model';
const displayName=(x:Entry)=>x.name;
// Recency is driven by real modelLifecycle.status (ACTIVE|LEGACY). The API has NO
// launch/creation date, so no date-accurate "last 6 months" filter is possible;
// lifecycle is the honest available signal and is never a fabricated date.
const isActive=(x:Entry)=>x.lifecycle==='ACTIVE';
const isLegacy=(x:Entry)=>x.lifecycle==='LEGACY';
type Snapshot={items:Entry[];count:number;mode:string;agent_listing_implemented:boolean;sources?:Record<string,{connection_state:string;reason?:string}>};
const categories=[{id:'model',label:'Models'},{id:'mcp_server',label:'MCP servers'},{id:'skill',label:'Skills'},{id:'agent',label:'Agents'},{id:'resource',label:'Other'}];
export default function AICatalog({api,onSelect}:{api:<T>(path:string,body?:unknown)=>Promise<T>;onSelect:(item:Entry)=>void}){
 const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[kind,setKind]=useState('model'),[query,setQuery]=useState(''),[error,setError]=useState(''),[selected,setSelected]=useState<Entry|null>(null),[detail,setDetail]=useState<Entry|null>(null),[purpose,setPurpose]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[modelCategory,setModelCategory]=useState('all');
 async function refresh(){setBusy(true);setError('');setSnapshot(null);setSelected(null);setDetail(null);try{setSnapshot(await api<Snapshot>('/catalog'));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 useEffect(()=>{void refresh();},[]);
 const all=snapshot?.items||[];
 const category=(x:Entry)=>x.kind==='mcp_server'&&(x.protocol!=='MCP'||x.supported===false)?'resource':x.kind;
 const resources=all.filter(x=>x.kind!=='tool'&&!x.parent_id);
 const children=(x:Entry)=>all.filter(c=>c.kind==='tool'&&c.parent_id===x.id&&c.protocol==='MCP');
 const items=resources.filter(x=>category(x)===kind&&(displayName(x)+' '+x.name+' '+x.description+' '+x.provider).toLowerCase().includes(query.toLowerCase()));
 const readiness=(x:Entry)=><StatusIndicator type={x.supported===false?'stopped':x.execution_ready?'success':'pending'}>{x.supported===false?'Unsupported':x.fixture?'Demo only':x.execution_ready?'Ready':'Not ready'}</StatusIndicator>;
 const access=(x:Entry)=><StatusIndicator type={x.usable?'success':x.requestable?'pending':'stopped'}>{stateText(x.status)}</StatusIndicator>;
 // Discoverable-vs-callable: a model can be listed (discoverable) yet not be
 // wired/granted/execution-ready. This badge reflects ONLY API-supplied state
 // (execution_ready / execution_binding / usable) and never fabricates a Ready
 // or granted state to unblock UI.
 const wiring=(x:Entry)=>{const ready=x.execution_ready===true;const bound=x.execution_binding?.status;return <Badge color={ready?'green':x.usable?'blue':'grey'}>{ready?'Execution-ready':x.usable?'Granted (execution unverified)':x.requestable?'Discoverable · requestable':bound?`Discoverable · ${bound}`:'Discoverable · not wired'}</Badge>;};
 // Recency badge from REAL modelLifecycle.status only. No launch date exists in
 // ListFoundationModels, so there is no date shown — ACTIVE is the honest
 // recency-adjacent signal, LEGACY marks superseded models.
 const recencyBadge=(x:Entry)=>{if(!isModel(x)||!x.lifecycle)return null;return <Badge color={isActive(x)?'green':'grey'}>{isActive(x)?'Active':'Legacy'}</Badge>;};
 const actions=(x:Entry,fullWidth=false)=><SpaceBetween direction={fullWidth?'vertical':'horizontal'} size="xs">{x.requestable&&<Button fullWidth={fullWidth} onClick={()=>{setSelected(x);setPurpose('');setNotice('');}}>Request access</Button>}{['model','tool','skill'].includes(x.kind)&&x.supported!==false&&(!x.fixture||x.usable)&&<Button fullWidth={fullWidth} onClick={()=>onSelect(x)}>{x.fixture?'Use in builder':'Add to draft'}</Button>}</SpaceBetween>;
 const summary=(description:string)=>{const sentence=description.trim().match(/^.*?[.!?](?=\s|$)|^.+$/s)?.[0]||'';return sentence.length>160?sentence.slice(0,157).trimEnd()+'…':sentence;};
 const metadata=(x:Entry)=><SpaceBetween size="s"><Box>Provider: {x.provider}</Box>{isModel(x)&&x.category&&<Box>Category: {categoryLabel(x.category)}</Box>}{isModel(x)&&x.lifecycle&&<Box>Lifecycle status: {lifecycleLabel(x.lifecycle)}{isActive(x)?' · current':' · superseded'}</Box>}{isModel(x)&&<Box>Launch date: not published by bedrock:ListFoundationModels</Box>}{isModel(x)&&x.input_modalities&&<Box>Input modalities: {x.input_modalities.join(', ')||'—'}</Box>}{isModel(x)&&x.output_modalities&&<Box>Output modalities: {x.output_modalities.join(', ')||'—'}</Box>}{isModel(x)&&x.streaming!==undefined&&<Box>Streaming: {x.streaming?'Supported':'Not supported'}</Box>}{isModel(x)&&x.inference_types&&x.inference_types.length>0&&<Box>Inference types: {x.inference_types.join(', ')}</Box>}<Box>Version: {x.version}</Box><Box>{x.data_handling}</Box></SpaceBetween>;
 const identifiers=(x:Entry)=><ExpandableSection headerText="Technical identifiers"><SpaceBetween size="xs"><Box variant="code">Catalog record: {x.record_id||x.id}</Box>{x.model_id&&<Box variant="code">Model / profile ID: {x.model_id}</Box>}{x.native_model_id&&<Box variant="code">Native model ID: {x.native_model_id}</Box>}{!x.model_id&&!x.native_model_id&&<Box variant="code">Source name: {x.name}</Box>}</SpaceBetween></ExpandableSection>;
 const nameCell=(x:Entry)=><SpaceBetween size="xxs"><Button variant="inline-link" onClick={()=>setDetail(x)}>{displayName(x)}</Button>{isModel(x)&&x.provider&&<Box variant="small">{x.provider}</Box>}{kind==='model'&&<SpaceBetween direction="horizontal" size="xxs">{recencyBadge(x)}{wiring(x)}</SpaceBetween>}</SpaceBetween>;
 const columns=[
  {id:'name',header:'Name',width:280,minWidth:240,cell:nameCell},
  {id:'description',header:'Description',width:360,minWidth:280,cell:(x:Entry)=><Box>{x.description}{category(x)==='mcp_server'&&<Box variant="small">{children(x).length} tools</Box>}</Box>},
  {id:'access',header:'Access',width:140,minWidth:140,cell:access}, {id:'readiness',header:'Readiness',width:140,minWidth:140,cell:readiness},
  {id:'action',header:'Action',width:180,minWidth:180,cell:(x:Entry)=><Button onClick={()=>setDetail(x)}>View details</Button>}
 ];
 const emptyFor=(label:string)=><Box>{!snapshot?(error?'Catalog unavailable.':'Loading catalog…'):resources.some(x=>category(x)===kind)?'No matching capabilities':kind==='model'?'No models available':label}</Box>;
 const flatTable=<Table wrapLines loading={busy} header={<Header counter={`(${items.length})`}>{categories.find(t=>t.id===kind)?.label}</Header>} items={items} columnDefinitions={columns} empty={emptyFor('No resources available')}/>;
 // Bedrock-style Models view: filter by category, then group BY PROVIDER. All
 // grouping/category facts come from real ListFoundationModels fields.
 const modelCategoryOf=(x:Entry):ModelCategory|'other'=>(x.category as ModelCategory)||'other';
 const modelItems=items.filter(x=>modelCategory==='all'||modelCategoryOf(x)===modelCategory);
 const providerOf=(x:Entry)=>x.provider||'Other providers';
 const providerOrder=Array.from(new Set(modelItems.map(providerOf))).sort((a,b)=>a==='Other providers'?1:b==='Other providers'?-1:a.localeCompare(b));
 const categoryOptions=[{id:'all',text:'All categories'},...(Object.keys(CATEGORY_LABELS) as ModelCategory[]).filter(c=>items.some(x=>modelCategoryOf(x)===c)).map(c=>({id:c,text:CATEGORY_LABELS[c]}))];
 const modelsView=<SpaceBetween size="m"><SegmentedControl label="Filter models by category" selectedId={modelCategory} onChange={({detail})=>setModelCategory(detail.selectedId)} options={categoryOptions}/>{modelItems.length===0?emptyFor('No models available'):providerOrder.map(p=><Table key={p} wrapLines loading={busy} variant="embedded" ariaLabels={{tableLabel:`${p} models`}} header={<Header counter={`(${modelItems.filter(x=>providerOf(x)===p).length})`}>{p}</Header>} items={modelItems.filter(x=>providerOf(x)===p)} columnDefinitions={columns} empty={emptyFor('No models available')}/>)}</SpaceBetween>;
 const contentFor=(tabId:string)=>tabId==='model'?modelsView:flatTable;
 return <SpaceBetween size="m"><Button iconName="refresh" disabled={busy} onClick={()=>void refresh()}>Refresh catalog</Button>{error&&<Alert type="error" header="Catalog unavailable">{actionText(error)} Try refreshing the catalog.</Alert>}{notice&&<Alert type="success">{notice}</Alert>}<Input ariaLabel="Search AI catalog" placeholder="Search resources" value={query} onChange={({detail})=>setQuery(detail.value)}/><Tabs activeTabId={kind} onChange={({detail})=>{setKind(detail.activeTabId);setDetail(null);setModelCategory('all');}} tabs={categories.map(t=>({...t,content:contentFor(t.id)}))}/>
 <Modal visible={!!detail} size="large" header={detail?displayName(detail):undefined} onDismiss={()=>{setDetail(null);setSelected(null);}} footer={<Button onClick={()=>{setDetail(null);setSelected(null);}}>Back to catalog</Button>}>{detail&&<SpaceBetween size="m"><Box>{detail.description}</Box>{metadata(detail)}{identifiers(detail)}{category(detail)==='mcp_server'?<SpaceBetween size="m"><Header counter={`(${children(detail).length})`}>Operations</Header>{children(detail).map(x=><div key={x.id} role="group" aria-label={`Operation: ${x.operation||x.name}`} data-testid="catalog-operation" data-operation-id={x.id}><Container header={<Header variant="h3">{x.operation||x.name}</Header>}><SpaceBetween size="s"><Box>{summary(x.description)}</Box><SpaceBetween direction="horizontal" size="m"><div aria-label="Access">{access(x)}</div><div aria-label="Readiness">{readiness(x)}</div></SpaceBetween>{actions(x,true)}<ExpandableSection headerText="Description and schemas"><SpaceBetween size="s"><Box>{x.description}</Box>{Object.entries(x.inputSchema?.properties||{}).map(([name,field])=><Box key={name}>{name}{x.inputSchema?.required?.includes(name)?' (required)':''}: {field.type||'See schema'} {field.description||''}</Box>)}<pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify({inputSchema:x.inputSchema,outputSchema:x.outputSchema},null,2)}</pre>{metadata(x)}</SpaceBetween></ExpandableSection></SpaceBetween></Container></div>)}{!children(detail).length&&<Box>No discoverable operations.</Box>}</SpaceBetween>:actions(detail)}{selected&&<SpaceBetween size="m"><Header variant="h2">Request access: {selected.operation||selected.name}</Header><FormField label="Workspace business purpose"><Textarea ariaLabel="Workspace business purpose" value={purpose} onChange={({detail})=>setPurpose(detail.value)}/></FormField><Button variant="primary" disabled={busy||purpose.trim().length<5} onClick={async()=>{setBusy(true);setError('');try{await api('/requests',{component_id:selected.id,reason:purpose});setNotice('Access request sent. Pending approval.');await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>Submit access request</Button></SpaceBetween>}</SpaceBetween>}</Modal></SpaceBetween>;
}
