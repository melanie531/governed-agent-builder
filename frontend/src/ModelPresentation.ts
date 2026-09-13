// Presentation-only helpers for model entries in the AI Catalog.
// No authorization, grant, usability, binding or readiness DECISION is made
// or altered here: these functions only translate actual backend fields into
// user-facing copy. Absent facts are reported as unknown, never invented.
export type ModelFacts={kind?:string;status?:string;requestable?:boolean;granted?:boolean;usable?:boolean;discovery_only?:boolean;policy_reason?:string;execution_ready?:boolean;execution_binding?:{status:string;last_checked:number|null}};

// Neutral, non-quantitative purpose summaries keyed on the exact native
// Bedrock model ID (same key space as VERIFIED_MODEL_DISPLAY, see
// backend/provider_model_metadata.py). No context-window, latency or
// benchmark claims: those are not evidenced by the catalog payload.
export const NEUTRAL_MODEL_DESCRIPTION:Record<string,string>={'anthropic.claude-haiku-4-5-20251001-v1:0':'Anthropic language model for everyday assistant tasks such as drafting, summarization and question answering. Access and execution are governed by this workspace.'};

// Explicit denial states are the ONLY inputs allowed to read as a refusal.
// A discovery-only entry whose status is the usable/requestable fallback
// ('blocked') is a catalog-availability fact, never a permission denial.
const DENIAL_STATES=new Set(['DENIED','REVOKED','REJECTED']);
export const isDenied=(x:ModelFacts):boolean=>DENIAL_STATES.has((x.status||'').toUpperCase())&&x.status!=='blocked';

// Access copy for models, strictly from actual backend fields:
// - explicit denial state       -> null (shared stateText renders the denial)
// - granted===true              -> 'Access granted' (independent of Ready)
// - requestable                 -> 'Available to request'
// - discovery_only, no grant    -> 'Not configured' (catalog can provide it,
//                                   this workspace has not connected it)
// - granted===false             -> 'Not granted'
// - no fact                     -> null (shared stateText mapping)
export const accessLabel=(x:ModelFacts):string|null=>{
 if(x.kind!=='model')return null;
 if(isDenied(x))return null;
 if(x.granted===true)return 'Access granted';
 if(x.requestable===true||x.status==='requestable')return 'Available to request';
 if(x.discovery_only===true)return 'Not configured';
 if(x.granted===false)return 'Not granted';
 return null;
};
export const accessTone=(x:ModelFacts):'success'|'pending'|'stopped'|'error'=>{
 if(x.kind==='model'){
  if(isDenied(x))return 'error';
  if(x.granted===true||x.usable===true)return 'success';
  if(x.requestable===true||x.status==='requestable')return 'pending';
  return 'stopped';
 }
 return x.usable?'success':x.requestable?'pending':'stopped';
};

// Real granted count: only granted===true counts, deduplicated by the stable
// model identity (model_id, falling back to record_id/id). Multiple routes to
// the same model never claim multiple granted models; distinct models are
// never merged by display name. All grants false -> 0.
export type ModelIdentity=ModelFacts&{model_id?:string;record_id?:string;id?:string};
export const modelIdentity=(m:ModelIdentity):string=>m.model_id||m.record_id||m.id||'';
export const grantedCount=(models:ModelIdentity[]):number=>new Set(models.filter(m=>m.granted===true).map(modelIdentity)).size;
export const modelCount=(models:ModelIdentity[]):number=>new Set(models.map(modelIdentity)).size;

// Explain "Not ready" strictly from the actual payload fields, separately:
// - granted===false        -> Access required (with the recorded policy reason)
// - granted===true         -> no access statement (granted)
// - granted missing        -> Access status unknown
// - execution_ready===false or execution_binding.status==='unverified'
//                          -> Execution not verified
// - execution_ready===true or execution_binding.status==='verified'
//                          -> no execution statement
// - both facts missing     -> Execution status unknown
export function readinessFacts(x:ModelFacts):string[]{
 const facts:string[]=[];
 if(x.granted===false)facts.push('Access required — '+(x.policy_reason||'Administrator grant required'));
 else if(x.granted===undefined)facts.push('Access status unknown');
 const binding=x.execution_binding?.status;
 if(x.execution_ready===true||binding==='verified'){/* verified: no statement */}
 else if(x.execution_ready===false||binding==='unverified')facts.push('Execution not verified');
 else facts.push('Execution status unknown');
 return facts;
}
