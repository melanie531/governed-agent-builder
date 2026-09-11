import {Alert, Box, KeyValuePairs, SpaceBetween, Table, Tabs} from '@cloudscape-design/components';
export type ResultEvidence = {
 schema_version:'result-evidence-v1'; source:'live'; status:'AVAILABLE'|'BLOCKED'; reason:string|null;
 quality_status?:'UNKNOWN'|'PASS'|'FAIL'|'INCOMPLETE'; required_judge_passed?:boolean; citation_status?:string;
 version:number; definition_digest:string; execution_status:string;
 report:{text:string;citations:{id:string;title:string;source_id:string}[]}|null;
 evaluations:{id:string;status:'PASS'|'FAIL'|'INCOMPLETE';completed:number;required:number;judge_complete:boolean}[];
 trace_ids:string[];
 cost:{status:'UNKNOWN'|'ESTIMATED';estimated_model_inference_cost:string|null;currency:string|null;
 usage:Record<string,number>|null;pricing_source:string|null;pricing_date:string|null;rate_version:string|null;
 model_route:string|null;model_route_version:string|null;unallocated:string[];scope:string};
};
export function ResultEvidenceView({value}:{value?:ResultEvidence}) {
 if(!value)return <Alert type="warning">Live evidence unavailable. No fixture fallback. Evidence reader integration is not configured.</Alert>;
 const cost=value.cost;
 return <SpaceBetween size="m"><Alert type={value.status==='AVAILABLE'?'info':'warning'} header={`Live evidence · ${value.status}`}>
 Quality: {value.quality_status||'UNKNOWN'}. Citations: {value.citation_status||'UNKNOWN'}. Immutable definition v{value.version}. {value.reason||'Verified server readback, not production acceptance.'}</Alert><Tabs tabs={[
 {id:'report',label:'Report',content:value.report?<section aria-label="Verified report"><pre>{value.report.text}</pre><ul>{value.report.citations.map(c=><li key={c.id}>[{c.id}] {c.title} · {c.source_id}</li>)}</ul></section>:<Box>Report unavailable / BLOCKED. No fixture replacement.</Box>},
 {id:'evaluation',label:'Evaluation',content:<Table items={value.evaluations} empty={<Box>Evaluation integration unavailable / BLOCKED</Box>} columnDefinitions={[
 {id:'id',header:'Evaluation ID',cell:e=>e.id},{id:'status',header:'Status',cell:e=>e.status},
 {id:'coverage',header:'Coverage',cell:e=>`${e.completed}/${e.required}`},{id:'judge',header:'Required judge',cell:e=>e.judge_complete?'Complete':'Missing / BLOCKED'}]}/>},
 {id:'execution',label:'Execution',content:<SpaceBetween size="m"><Box>{value.execution_status}</Box><Box>Trace IDs: {value.trace_ids.join(', ')||'Unavailable'}</Box><Box>Trace console/log drilldown integration unavailable. Raw prompts and logs are not exposed.</Box></SpaceBetween>},
 {id:'cost',label:'Cost',content:<SpaceBetween size="m"><KeyValuePairs items={[
 {label:'Estimated model inference cost',value:cost.status==='ESTIMATED'?`${cost.currency} ${cost.estimated_model_inference_cost}`:'UNKNOWN'},
 {label:'Provider usage',value:cost.usage?Object.entries(cost.usage).map(([k,v])=>`${k}: ${v}`).join(' · '):'UNKNOWN'},
 {label:'Pricing source / date',value:`${cost.pricing_source||'UNKNOWN'} / ${cost.pricing_date||'UNKNOWN'}`},
 {label:'Rate schedule version',value:cost.rate_version||'UNKNOWN'},
 {label:'Model route / version',value:`${cost.model_route||'UNKNOWN'} / ${cost.model_route_version||'UNKNOWN'}`} ]}/><Box>{cost.scope}</Box><Alert type="warning">Unallocated charges: {cost.unallocated.join(', ')}. Budget reservation is not cost or invoice evidence.</Alert></SpaceBetween>}
 ]}/></SpaceBetween>;
}
