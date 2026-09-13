// Presentation only: server authorization and readiness decisions are unchanged.
export function stateText(value:string):string {
 const labels:Record<string,string>={PENDING:'Pending approval',PENDING_APPROVAL:'Pending approval',APPROVED:'Approved',REJECTED:'Rejected',BLOCKED:'Blocked',DENIED:'Denied',REVOKED:'Revoked',SUBMITTED:'Submitted',IN_PROGRESS:'In progress',RESOLVED:'Resolved',CLOSED:'Closed',NOT_CONFIGURED:'Not deployed',NOT_CONNECTED:'Not connected',NotConnected:'Not connected',UNKNOWN:'Not available',INCOMPLETE:'Evaluation not complete',NOT_RUN:'Evaluation not run',PASS:'Sample test passed',FAIL:'Failed',LIVE_PASS:'Live checks passed',LOCAL_RUNTIME_READY:'Sample test prepared',WAIT_RUNTIME:'Waiting for AgentCore Runtime',NEEDS_CHANGES:'Changes needed',AVAILABLE:'Available',VALIDATING:'Validating',RUNNING:'Running',EVALUATING:'Evaluating',TESTING:'Testing',PREPARING:'Preparing',EVIDENCE_CHECK:'Checking results'};
 return labels[value]||(/^[A-Z][A-Z0-9_]+$/.test(value)?'Needs attention':value.replaceAll('_',' '));
}
export function actionText(value:string):string {
 if(/LimitExceeded|TooManyRequests|rate.limit/i.test(value))return 'Too many attempts. Wait a moment and try again.';
 if(/CodeMismatch|invalid.*code/i.test(value))return 'Check the verification code and try again.';
 if(/ExpiredCode/i.test(value))return 'This code has expired. Request a new code.';
 if(/NOT_CONFIGURED|NotConnected|NOT_CONNECTED|binding|integration|connector/i.test(value))return 'This capability is not ready. Choose another capability or contact your administrator.';
 if(/AccessDenied|Forbidden|grant|permission|policy/i.test(value))return 'Access is restricted. Request access or contact your administrator.';
 if(/Exception|Traceback|\{.*\}|[A-Z]{2,}_[A-Z_]{2,}/.test(value))return 'The action could not be completed. Try again or contact your administrator.';
 return value;
}

// Only known audit actions and typed fields are rendered; free text stays private.
function fields(value:unknown):Record<string,unknown> {
 if(typeof value==='string'){try{value=JSON.parse(value);}catch{return {};}}
 return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
}
export function auditSummary(action:string,detail:unknown):string {
 const d=fields(detail);
 const version=typeof detail==='string'&&/^\d+(?:\.\d+)*$/.test(detail)?detail:null;
 switch(action){
 case 'policy_revision':return version?`Policy version ${version} saved`:'Policy revision saved';
 case 'catalog_revision':return version?`Catalog version ${version} saved`:'Catalog revision saved';
 case 'grant':return 'Access granted';
 case 'revoke':return 'Access revoked';
 case 'request_decided':return d.decision==='approved'?'Request approved':d.decision==='rejected'?'Request rejected':'Request decision recorded';
 case 'definition_created':{const v=typeof detail==='string'?/^version=(\d+)(?:,|$)/.exec(detail):null;return v?`Definition version ${v[1]} saved`:'Definition saved';}
 case 'export':return 'Source archive exported';
 case 'deploy_test':return 'Evaluation requested';
 case 'local_invoke':return 'Simulation invoked';
 case 'capability_requested':return 'Capability access requested';
 case 'tool_requested':return typeof d.title==='string'?`Tool requested: ${d.title}`:'New tool requested';
 case 'tool_request_responded':return [d.status,d.response].filter(value=>typeof value==='string').join(' · ');
 case 'model_registered':case 'model_validated':case 'registry_registered':
 case 'registry_submitted':case 'registry_decided':case 'catalog_published':case 'catalog_withdrawn':case 'journey_policy_changed':
   return [d.status,d.reason].filter(value=>typeof value==='string').join(' · ')||'Governance decision recorded';
 case 'demo_session':return 'Session selected';
 default:return 'Activity recorded';
 }
}
export function eventSummary(stage:string,detail:unknown):string {
 const d=fields(detail);
 const messages:Record<string,string>={
 'Live deployment reserved':'Deployment request accepted',
 'Local deploy/test accepted':'Simulation request accepted',
 'Pinned fixture harness loaded':'Pinned simulation configuration loaded',
 'Local fixture runner ready (not AWS Runtime)':'Simulation environment prepared',
 'Bounded synthetic cases scheduled':'Evaluation cases scheduled',
 'Executing fixture cases and deterministic checks':'Simulation cases and checks started',
 'Resumed durable local job after process restart':'Saved evaluation resumed'};
 const parts:string[]=[];
 if(typeof d.message==='string'&&Object.hasOwn(messages,d.message))parts.push(messages[d.message]);
 if(typeof d.score==='number'&&Number.isFinite(d.score)&&d.score>=0&&d.score<=1)parts.push(`Measured score: ${Math.round(d.score*100)} / 100`);
 if(stage==='CASE_EVIDENCE')parts.unshift('Evaluation case recorded');
 if(!parts.length){const fallback:Record<string,string>={BLOCKED:'Execution could not proceed',DENIED:'Execution authorization refused',REVOKED:'Execution authorization withdrawn',PASS:'Simulation checks completed',LIVE_PASS:'Live checks completed',NEEDS_CHANGES:'Evaluation requires revision'};parts.push(fallback[stage]||'Event recorded; no displayable evidence');}
 return parts.join(' · ');
}
