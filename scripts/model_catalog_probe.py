"""Exact metadata-only interceptor release and one GET; never permits inference."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import zipfile

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from scripts.foundation_target import StudioTarget, STACK, sanitized
from scripts.model_gate_probe import inventory, check_quarantine, load_template

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'artifacts/model-catalog'


def save(name, value):
    WORK.mkdir(exist_ok=True)
    path = WORK / (name + '.json')
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(value, default=str, indent=2))


def main():
    t = StudioTarget(); t.verify()
    cf = t.client('cloudformation'); ids = inventory(cf)
    g = check_quarantine(t, ids)
    old = load_template(cf, StackName=STACK)
    desired = copy.deepcopy(old)
    source = (ROOT / 'tools/model_gate/handler.py').read_text()
    desired['Resources']['ModelGate']['Properties']['Code'] = {'ZipFile': source}
    assert [k for k in old['Resources'] if old['Resources'][k] != desired['Resources'][k]] == ['ModelGate']
    assert not (WORK / 'metadata-change-executed.json').exists(), 'Already executed; read receipt'
    save('metadata-rollback-template', old)
    save('metadata-desired-template', desired)
    cs = 'model-metadata-' + str(time.time_ns())
    stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
    t.verify()
    cf.create_change_set(StackName=STACK, ChangeSetName=cs, ChangeSetType='UPDATE',
        TemplateBody=json.dumps(desired), Capabilities=['CAPABILITY_NAMED_IAM'],
        Parameters=[{'ParameterKey':p['ParameterKey'], 'UsePreviousValue':True} for p in stack.get('Parameters',[])])
    for _ in range(30):
        change = cf.describe_change_set(StackName=STACK, ChangeSetName=cs)
        if change['Status'] not in ('CREATE_PENDING','CREATE_IN_PROGRESS'): break
        time.sleep(2)
    save('metadata-change-review',change)
    assert change['Status']=='CREATE_COMPLETE' and not change.get('NextToken')
    from scripts.model_gate_review import verify_live
    before_semantics = verify_live(t, old, ids)
    dependencies = {
        'FoundationRole': {('Policies', 'ModelGateway.GatewayArn')},
        'ModelGateway': {('RoleArn', 'ModelRole.Arn'), ('InterceptorConfigurations', 'ModelGate.Arn')},
        'ModelRole': {('Policies', 'ModelGate.Arn')},
        'ToolPolicy': {('Definition', 'FoundationRole.Arn')},
    }
    seen = set()
    for item in change['Changes']:
        r = item['ResourceChange']; name = r['LogicalResourceId']
        assert name not in seen; seen.add(name)
        assert r['Action']=='Modify' and r['Replacement']=='False'
        if name == 'ModelGate':
            assert all(d['Target']['Name']=='Code' and d['Target']['RequiresRecreation']=='Never' for d in r['Details'])
        else:
            assert name in dependencies and old['Resources'][name] == desired['Resources'][name]
            assert {(d['Target']['Name'], d.get('CausingEntity')) for d in r['Details']} == dependencies[name]
            assert all(d['Evaluation']=='Dynamic' and d['ChangeSource']=='ResourceAttribute' and d['Target']['RequiresRecreation']=='Never' for d in r['Details'])
    assert 'ModelGate' in seen
    t.verify(); check_quarantine(t,ids)
    assert verify_live(t, desired, ids) == before_semantics
    save('metadata-change-executed',{'name':cs,'state':'DISPATCHING','sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()})
    cf.execute_change_set(StackName=STACK,ChangeSetName=cs)
    for _ in range(45):
        after=cf.describe_stacks(StackName=STACK)['Stacks'][0]
        if not after['StackStatus'].endswith('IN_PROGRESS'):break
        time.sleep(3)
    assert after['StackStatus']=='UPDATE_COMPLETE'
    assert inventory(cf)==ids and after['Outputs']==stack['Outputs']
    assert load_template(cf,StackName=STACK)==desired
    assert verify_live(t, desired, ids) == before_semantics
    f=t.client('lambda').get_function(FunctionName=ids['ModelGate'])
    raw=urllib.request.urlopen(f['Code']['Location']).read()
    assert zipfile.ZipFile(io.BytesIO(raw)).read('index.py')==source.encode()
    g=check_quarantine(t,ids)
    d=t.client('bedrock-agentcore-control').get_gateway_target(gatewayIdentifier=g['gatewayId'],targetId=ids['ModelTarget'].split('|')[-1])
    save('target',d)
    url='https://'+g['gatewayId']+'.gateway.bedrock-agentcore.us-west-2.amazonaws.com/inference/v1/models'
    req=AWSRequest(method='GET',url=url)
    SigV4Auth(t.session.get_credentials().get_frozen_credentials(),'bedrock-agentcore','us-west-2').add_auth(req)
    response=httpx.get(url,headers=dict(req.headers),timeout=20,follow_redirects=False)
    receipt={'http':response.status_code,'body':response.json(),'source_sha256':hashlib.sha256(source.encode()).hexdigest(), 'deployed_zip_sha256':hashlib.sha256(raw).hexdigest(),'physical_ids_preserved':True,'policies_preserved':True,'positive_inference_calls':0}
    save('metadata-readback',receipt)
    print(json.dumps(receipt),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(sanitized(e),flush=True)
        raise SystemExit(1) from None
