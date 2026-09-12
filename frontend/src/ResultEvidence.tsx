import {actionText, stateText} from './ProductText';
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
 if(!value)return <Alert type="warning">Results unavailable. Try again or contact your administrator.</Alert>;
 const cost=value.cost;
 return <SpaceBetween size="m"><Alert type={value.status==='AVAILABLE'?'info':'warning'} header={`Live results · ${stateText(value.status)}`}>
 Quality: {stateText(value.quality_status||'UNKNOWN')}. Citations: {stateText(value.citation_status||'UNKNOWN')}. Version {value.version}. {value.status==='BLOCKED'&&'Results need attention. Try again or contact your administrator.'}</Alert><Tabs tabs={[
 {id:'report',label:'Report',content:value.report?<section aria-label="Verified report"><pre>{value.report.text}</pre><ul>{value.report.citations.map(c=><li key={c.id}>[{c.id}] {c.title} · {c.source_id}</li>)}</ul></section>:<Box>Report unavailable.</Box>},
 {id:'evaluation',label:'Evaluation',content:<Table items={value.evaluations} empty={<Box>Evaluation not run</Box>} columnDefinitions={[
 {id:'id',header:'Evaluation ID',cell:e=>e.id},{id:'status',header:'Status',cell:e=>stateText(e.status)},
 {id:'coverage',header:'Coverage',cell:e=>`${e.completed}/${e.required}`},{id:'judge',header:'Required judge',cell:e=>e.judge_complete?'Complete':'Not complete'}]}/>},
 {id:'execution',label:'Execution',content:<SpaceBetween size="m"><Box>{stateText(value.execution_status)}</Box><Box>Trace IDs: {value.trace_ids.join(', ')||'Unavailable'}</Box></SpaceBetween>},
 {id:'cost',label:'Cost',content:<SpaceBetween size="m"><KeyValuePairs items={[
 {label:'Estimated model inference cost',value:cost.status==='ESTIMATED'?`${cost.currency} ${cost.estimated_model_inference_cost}`:'Not available'},
 {label:'Provider usage',value:cost.usage?Object.entries(cost.usage).map(([k,v])=>`${k}: ${v}`).join(' · '):'Not available'},
 {label:'Pricing source / date',value:`${cost.pricing_source||'Not available'} / ${cost.pricing_date||'Not available'}`},
 {label:'Rate schedule version',value:cost.rate_version||'Not available'},
 {label:'Model route / version',value:`${cost.model_route||'Not available'} / ${cost.model_route_version||'Not available'}`} ]}/>{cost.unallocated.length>0&&<Box>Some charges are not included in this estimate.</Box>}</SpaceBetween>}
 ]}/></SpaceBetween>;
}
