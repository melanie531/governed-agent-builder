"""Bounded owned-stack install and negative transport probe. NEVER grants inference.

prepare -> inspect persisted ChangeSet -> execute -> negative. Operator SigV4
is transport testing, NOT workload/human authority proof. No provider retries.
"""
import argparse
import base64
import io
import zipfile
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from infra.model_gate import overlay, inspect_change_set
from scripts.model_gate_review import verify_live
from scripts.foundation_target import StudioTarget, STACK, PROJECT, REGION
from tools.model_gate.handler import APPROVED_MODEL

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / 'artifacts/model-gate/proof.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_sha():
    return subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()


def save(proof):
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.touch(mode=0o600, exist_ok=True)
    RECEIPT.chmod(0o600)
    RECEIPT.write_text(json.dumps(proof, indent=2))


def load_template(cf, **kwargs):
    body = cf.get_template(TemplateStage='Original', **kwargs)['TemplateBody']
    return json.loads(body) if isinstance(body,str) else body


def inventory(cf):
    response = cf.list_stack_resources(StackName=STACK)
    if response.get('NextToken'): raise ValueError('INVENTORY_PAGINATION')
    return {r['LogicalResourceId']: r['PhysicalResourceId'] for r in response['StackResourceSummaries']}


def check_quarantine(target, ids):
    iam = target.client('iam')
    policy = iam.get_role_policy(RoleName=ids['ModelRole'], PolicyName='owned-foundation-only')['PolicyDocument']
    if not any(s['Effect']=='Deny' and s['Resource']=='*' and 'bedrock-mantle:CreateInference' in s['Action'] for s in policy['Statement']):
        raise ValueError('INFERENCE_DENY_REQUIRED')
    gateway = target.client('bedrock-agentcore-control').get_gateway(gatewayIdentifier=ids['ModelGateway'])
    if gateway['authorizerType']!='AWS_IAM' or gateway['policyEngineConfiguration']['mode']!='ENFORCE' or gateway['status']!='READY':
        raise ValueError('GATEWAY_GUARDS_REQUIRED')
    return gateway


def safe_id(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9-]{8,80}',value) and not re.search(r'\d{12}',value) else None


def negative_cases(body):
    return [('unknown_model',json.dumps({**body,'model':'unapproved/model'}).encode()),
            ('bad_json',b'{'), ('stream_true',json.dumps({**body,'stream':True}).encode()),
            ('excessive_max_tokens',json.dumps({**body,'max_tokens':257}).encode())]


def run(action):
    target = StudioTarget()
    verified = target.verify()
    cf = target.client('cloudformation')
    stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
    tags = {t['Key']:t['Value'] for t in stack.get('Tags',[])}
    if (stack['StackId'].split(':')[3:5] != [REGION,target.account] or stack['StackStatus']!='UPDATE_COMPLETE'
            or tags.get('project')!=PROJECT or tags.get('scope')!='foundation-m0'):
        raise ValueError('OWNED_STABLE_STACK_REQUIRED')
    ids = inventory(cf)
    gateway = check_quarantine(target, ids)
    old = load_template(cf, StackName=STACK)
    if action=='prepare':
        if RECEIPT.exists(): raise ValueError('EXISTING_RECEIPT_REVIEW')
        desired = overlay(old)
        proof = {'source_sha':source_sha(), 'status':'PREPARING', 'target':verified,
                 'before_digest':digest(old), 'desired_digest':digest(desired),
                 'change_set':'model-gate-'+str(int(time.time())), 'gateway_probe_count':0,
                 'provider_inference_grants':0, 'positive_inference_attempts':0,
                 'provider_invocation_observed':'UNKNOWN', 'actual_cost_usd':None,
                 'remaining_budget_usd':'UNKNOWN; prior estimate <2, reserve 1 before positive',
                 'fault_fail_closed':'UNPROVEN', 'platform_user_authorization':'NOT_PROVEN'}
        save(proof)
        target.verify()
        response = cf.create_change_set(StackName=STACK, ChangeSetName=proof['change_set'], ChangeSetType='UPDATE',
            TemplateBody=json.dumps(desired), Capabilities=['CAPABILITY_NAMED_IAM'], Tags=stack['Tags'],
            Parameters=[{'ParameterKey':p['ParameterKey'],'UsePreviousValue':True} for p in stack.get('Parameters',[])])
        proof['create_request_id']=response['ResponseMetadata']['RequestId']; save(proof)
        for _ in range(18):
            change=cf.describe_change_set(StackName=STACK,ChangeSetName=proof['change_set'])
            if change['Status'] not in ('CREATE_PENDING','CREATE_IN_PROGRESS'): break
            time.sleep(5)
        proof['changes']=[{k:r.get(k) for k in ('LogicalResourceId','ResourceType','Action','Replacement','Details')}
                          for r in (x['ResourceChange'] for x in change.get('Changes',[]))]
        save(proof)
        review = verify_live(target, old, ids)
        inspect_change_set(change, old, desired, review)
        if load_template(cf,StackName=STACK,ChangeSetName=proof['change_set'])!=desired:
            raise ValueError('CHANGESET_TEMPLATE_MISMATCH')
        proof['resolved_review']=review
        proof['status']='REVIEWED_NOT_EXECUTED'; save(proof)
        return proof
    proof=json.loads(RECEIPT.read_text())
    if action=='execute':
        if proof['source_sha']!=source_sha() or proof['status']!='REVIEWED_NOT_EXECUTED' or digest(old)!=proof['before_digest']:
            raise ValueError('SOURCE_OR_STACK_CHANGED')
        desired=overlay(old)
        if digest(desired)!=proof['desired_digest']: raise ValueError('DESIRED_CHANGED')
        change=cf.describe_change_set(StackName=STACK,ChangeSetName=proof['change_set'])
        review = verify_live(target, old, ids)
        inspect_change_set(change, old, desired, review)
        if load_template(cf,StackName=STACK,ChangeSetName=proof['change_set'])!=desired:
            raise ValueError('CHANGESET_TEMPLATE_MISMATCH')
        if review != {k:v for k,v in proof['resolved_review'].items() if k != 'unchanged_dependency_definitions'}:
            raise ValueError('LIVE_REVIEW_CHANGED')
        target.verify(); check_quarantine(target,ids)
        proof['status']='EXECUTION_OUTCOME_UNKNOWN'; save(proof)
        response=cf.execute_change_set(StackName=STACK,ChangeSetName=proof['change_set'])
        proof['execute_request_id']=response['ResponseMetadata']['RequestId']; save(proof)
        for _ in range(30):
            current=cf.describe_stacks(StackName=STACK)['Stacks'][0]
            proof['status']=current['StackStatus']; save(proof)
            if not current['StackStatus'].endswith('IN_PROGRESS'): break
            time.sleep(5)
        if proof['status']!='UPDATE_COMPLETE': raise ValueError('REVIEW_STACK_NO_RETRY')
        if load_template(cf,StackName=STACK)!=desired or current['Outputs']!=stack['Outputs']:
            raise ValueError('READBACK_MISMATCH')
        after=inventory(cf)
        if any(after[k]!=v for k,v in ids.items()): raise ValueError('PHYSICAL_ID_CHANGED')
        gw=check_quarantine(target,after)
        config=gw.get('interceptorConfigurations',[])
        if len(config)!=1 or config[0]['interceptionPoints']!=['REQUEST'] or config[0]['inputConfiguration']['passRequestHeaders'] is not False:
            raise ValueError('INTERCEPTOR_READBACK_MISMATCH')
        lam=target.client('lambda').get_function_configuration(FunctionName=after['ModelGate'])
        if config[0]['interceptor']['lambda']['arn']!=lam['FunctionArn'] or lam['State']!='Active':
            raise ValueError('FUNCTION_BINDING_MISMATCH')
        post_review = verify_live(target, desired, after)
        for name in ('FoundationRole', 'ToolPolicy', 'ToolsRole', 'ToolsGateway', 'ModelGateway', 'FixtureRole'):
            if post_review[name] != review[name]:
                raise ValueError('UNCHANGED_RESOURCE_READBACK_MISMATCH')
        function = target.client('lambda').get_function(FunctionName=after['ModelGate'])
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            zipped = client.get(function['Code']['Location'])
            zipped.raise_for_status()
            blob = zipped.content
        if base64.b64encode(hashlib.sha256(blob).digest()).decode() != function['Configuration']['CodeSha256']:
            raise ValueError('LAMBDA_ZIP_HASH_MISMATCH')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            if archive.namelist() != ['index.py'] or archive.read('index.py') != desired['Resources']['ModelGate']['Properties']['Code']['ZipFile'].encode():
                raise ValueError('LAMBDA_SOURCE_MISMATCH')
        proof['lambda_zip_sha256'] = hashlib.sha256(blob).hexdigest()
        proof['lambda_source_sha256'] = hashlib.sha256(desired['Resources']['ModelGate']['Properties']['Code']['ZipFile'].encode()).hexdigest()
        proof['post_review'] = post_review
        proof.update(status='DEPLOYED_DENY_RETAINED',physical_ids='EXISTING_UNCHANGED',interceptor='EXACT_REQUEST_NO_HEADERS',target_after=target.verify())
        save(proof); return proof
    if proof['status']!='DEPLOYED_DENY_RETAINED' or proof.get('negative_started'):
        raise ValueError('PROBE_NOT_READY_OR_ALREADY_ATTEMPTED')
    url=gateway['gatewayUrl'].removesuffix('/mcp')+'/inference/v1/messages'
    if not re.fullmatch(r'https://gab-foundation-model-m0-[a-z0-9]+\.gateway\.bedrock-agentcore\.us-west-2\.amazonaws\.com/inference/v1/messages',url):
        raise ValueError('EXACT_GATEWAY_URL_REQUIRED')
    probe_body={'model':APPROVED_MODEL,'messages':[{'role':'user','content':'Synthetic gate check'}],'max_tokens':1,'stream':False}
    cases=negative_cases(probe_body)
    proof['negative_started']=True; proof['probes']=[]; proof['slice_gateway_attempts']=0; save(proof)
    for name,body in cases:
        # Persist the irreversible attempt BEFORE transport; never retry unknown outcomes.
        check_quarantine(target,ids)
        proof['gateway_probe_count']+=1; proof['slice_gateway_attempts']+=1
        proof['probes'].append({'case':name, 'outcome':'UNKNOWN'})
        save(proof)
        request=AWSRequest(method='POST',url=url,data=body,headers={'Content-Type':'application/json','anthropic-version':'2023-06-01'})
        credentials=target.session.get_credentials()
        if credentials is None: raise ValueError('OPERATOR_CREDENTIALS_UNAVAILABLE')
        SigV4Auth(credentials.get_frozen_credentials(),'bedrock-agentcore',REGION).add_auth(request)
        with httpx.Client(timeout=12,follow_redirects=False,trust_env=False) as client:
            with client.stream('POST',url,content=body,headers=dict(request.headers)) as response:
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>16384: raise ValueError('RESPONSE_CAP')
                # Never persist raw upstream error bodies, prompts, IDs or headers.
                code=None
                try:
                    value=json.loads(raw); error=value.get('error',{})
                    candidate=error.get('code') if isinstance(error,dict) else None
                    if candidate in ('MODEL_GATE_DENIED','access_denied','AccessDeniedException','InternalServerException'):
                        code=candidate
                except (ValueError,AttributeError): pass
                proof['probes'][-1].update({'case':name,'outcome':'RESPONSE','status':response.status_code,'safe_code':code,
                    'response_digest':hashlib.sha256(raw).hexdigest(),
                    'request_id':safe_id(response.headers.get('x-amzn-requestid') or response.headers.get('x-amzn-request-id'))})
                save(proof)
                if 200<=response.status_code<300: raise ValueError('UNEXPECTED_SUCCESS_STOP')
                if response.status_code==403 and code is None:
                    proof['transport_admission']='UNPROVEN_OR_OPERATOR_DENIED'; save(proof)
                    break
    # Read ONLY sanitized decision records from the new exact interceptor log.
    logs=target.client('logs').filter_log_events(logGroupName='/aws/lambda/gab-foundation-m0-model-gate',
        startTime=int((time.time()-600)*1000),limit=50)
    proof['interceptor_logs']=[]
    for e in logs.get('events',[]):
        match=re.search(r'\{"decision": "(ALLOW|MODEL_GATE_DENIED)", "gateway_request_id": (null|"[a-fA-F0-9-]{16,64}")\}',e['message'])
        if match: proof['interceptor_logs'].append(json.loads(match.group()))
    proof['logs_truncated']=bool(logs.get('nextToken'))
    for probe in proof['probes']:
        probe['correlated_decisions']=[e['decision'] for e in proof['interceptor_logs'] if probe.get('request_id') and e['gateway_request_id']==probe['request_id']]
    proof['status']='NEGATIVE_OBSERVED_FAULT_PROOF_PENDING'; save(proof)
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','execute','negative'])
    args=parser.parse_args()
    try:
        print(json.dumps(run(args.action),indent=2))
    except Exception as exc:
        # Do not serialize service exception text: it can contain accounts/payloads.
        code=str(exc) if type(exc) is ValueError and re.fullmatch('[A-Z_]+',str(exc)) else type(exc).__name__
        if RECEIPT.exists():
            proof=json.loads(RECEIPT.read_text()); proof['blocker']=code; save(proof)
        print(json.dumps({'blocked':code,'receipt':str(RECEIPT.relative_to(ROOT))}))
        raise SystemExit(1) from None
