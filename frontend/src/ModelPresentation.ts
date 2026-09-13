// Presentation-only helpers for model entries in the AI Catalog.
// No authorization, grant, usability, binding or readiness DECISION is made
// or altered here: these functions only translate actual backend fields into
// user-facing copy. Absent facts are reported as unknown, never invented.
export type ModelFacts={kind?:string;status?:string;requestable?:boolean;granted?:boolean;policy_reason?:string;execution_ready?:boolean;execution_binding?:{status:string;last_checked:number|null}};

// Neutral, non-quantitative purpose summaries keyed on the exact native
// Bedrock model ID (same key space as VERIFIED_MODEL_DISPLAY, see
// backend/provider_model_metadata.py). No context-window, latency or
// benchmark claims: those are not evidenced by the catalog payload.
export const NEUTRAL_MODEL_DESCRIPTION:Record<string,string>={'anthropic.claude-haiku-4-5-20251001-v1:0':'Anthropic language model for everyday assistant tasks such as drafting, summarization and question answering. Access and execution are governed by this workspace.'};

// Models with status==='requestable' read as an actionable availability
// statement; every other status keeps the shared stateText mapping.
export const accessLabel=(x:ModelFacts):string|null=>x.kind==='model'&&x.status==='requestable'?'Available to request':null;

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
