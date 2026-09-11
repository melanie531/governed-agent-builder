"""Bounded exact FoundationRole policy update. No Runtime creation or invocation.

Run --prepare, review saved receipt and exact SHA, then --execute. Never retries
unknown write outcomes. Receipts are private local files; account IDs redacted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from scripts.admission_role_policy import overlay, inspect_change_set, exact_invoke_arn, POLICY_NAME
from scripts.foundation_target import StudioTarget, STACK, PROJECT, REGION, sanitized

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / 'artifacts/foundation-admission/role-update-proof.json'


def sha():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(proof):
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.touch(mode=0o600, exist_ok=True)
    RECEIPT.chmod(0o600)
    RECEIPT.write_text(json.dumps(proof, indent=2))


def template(cf, **kw):
    body = cf.get_template(TemplateStage='Original', **kw)['TemplateBody']
    return json.loads(body) if isinstance(body, str) else body


def run(action):
    target = StudioTarget()
    verified = target.verify()
    cf = target.client('cloudformation')
    stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
    tags = {x['Key']: x['Value'] for x in stack.get('Tags', [])}
    assert stack['StackId'].split(':')[3:5] == [REGION, target.account]
    assert stack['StackStatus'] == 'UPDATE_COMPLETE' and tags.get('project') == PROJECT and tags.get('scope') == 'foundation-m0'
    app = cf.describe_stacks(StackName=PROJECT+'-serverless-app')['Stacks'][0]
    outputs = {x['OutputKey']:x['OutputValue'] for x in app['Outputs']}
    api = outputs['ApiEndpoint'].split('//')[1].split('.')[0]
    routes = target.client('apigatewayv2').get_routes(ApiId=api)
    assert not routes.get('NextToken')
    route = [r for r in routes['Items'] if r['RouteKey'] == 'POST /internal/foundation/exchange']
    assert len(route) == 1 and route[0]['AuthorizationType'] == 'AWS_IAM'
    old = template(cf, StackName=STACK)
    desired = overlay(old, api)
    resources = cf.list_stack_resources(StackName=STACK)
    assert not resources.get('NextToken')
    role_name = next(r['PhysicalResourceId'] for r in resources['StackResourceSummaries'] if r['LogicalResourceId']=='FoundationRole')
    iam = target.client('iam')
    role = iam.get_role(RoleName=role_name)['Role']
    assert role['Arn'] == f'arn:aws:iam::{target.account}:role/{role_name}'
    trust_digest = fingerprint(role['AssumeRolePolicyDocument'])
    if action == 'prepare':
        assert not RECEIPT.exists(), 'EXISTING_RECEIPT_REVIEW_REQUIRED'
        assert desired != old, 'ALREADY_CONFIGURED_READBACK_REQUIRED'
        proof = {'source_sha':sha(), 'status':'PREPARING', 'target':verified,
                 'before_template_digest':fingerprint(old), 'desired_template_digest':fingerprint(desired),
                 'trust_digest_before':trust_digest, 'runtime_create_count':0, 'runtime_invoke_count':0,
                 'workload_admission':'NOT_PROVEN', 'actual_cost_usd':None}
        proof['change_set'] = 'foundation-role-admission-'+str(int(time.time()))
        save(proof)
        target.verify()
        response = cf.create_change_set(StackName=STACK, ChangeSetName=proof['change_set'], ChangeSetType='UPDATE',
            TemplateBody=json.dumps(desired), Capabilities=['CAPABILITY_NAMED_IAM'], Tags=stack['Tags'],
            Parameters=[{'ParameterKey':p['ParameterKey'],'UsePreviousValue':True} for p in stack.get('Parameters',[])],
            Description='Exact existing admission POST allow and explicit direct Lambda deny; trust unchanged')
        proof['create_request_id'] = response['ResponseMetadata']['RequestId']
        save(proof)
        for _ in range(18):
            change = cf.describe_change_set(StackName=STACK, ChangeSetName=proof['change_set'])
            if change['Status'] not in ('CREATE_PENDING','CREATE_IN_PROGRESS'):
                break
            time.sleep(5)
        proof['observed_changes'] = [{k:r.get(k) for k in (
            'LogicalResourceId', 'ResourceType', 'Action', 'Replacement', 'Details')}
            for r in (item['ResourceChange'] for item in change.get('Changes', []))]
        save(proof)
        try:
            proof['change'] = inspect_change_set(change, STACK)
        except ValueError:
            proof['status'] = 'BLOCKED_UNEXPECTED_CHANGESET_NOT_EXECUTED'
            save(proof)
            raise
        assert template(cf, StackName=STACK, ChangeSetName=proof['change_set']) == desired
        proof['status'] = 'REVIEWED_NOT_EXECUTED'
        save(proof)
        return proof
    proof = json.loads(RECEIPT.read_text())
    assert proof['status'] == 'REVIEWED_NOT_EXECUTED'
    assert proof['source_sha'] == sha(), 'SOURCE_CHANGED'
    assert fingerprint(old) == proof['before_template_digest']
    assert fingerprint(desired) == proof['desired_template_digest']
    assert trust_digest == proof['trust_digest_before']
    change = cf.describe_change_set(StackName=STACK, ChangeSetName=proof['change_set'])
    inspect_change_set(change, STACK)
    assert template(cf, StackName=STACK, ChangeSetName=proof['change_set']) == desired
    target.verify()
    proof['status'] = 'EXECUTION_OUTCOME_UNKNOWN'
    save(proof)
    response = cf.execute_change_set(StackName=STACK, ChangeSetName=proof['change_set'])
    proof['execute_request_id'] = response['ResponseMetadata']['RequestId']
    save(proof)
    for _ in range(24):
        updated = cf.describe_stacks(StackName=STACK)['Stacks'][0]
        proof['status'] = updated['StackStatus']
        save(proof)
        if not updated['StackStatus'].endswith('IN_PROGRESS'):
            break
        time.sleep(5)
    assert proof['status'] == 'UPDATE_COMPLETE', 'REVIEW_PERSISTED_STACK_OUTCOME_NO_RETRY'
    assert template(cf, StackName=STACK) == desired
    assert updated['Outputs'] == stack['Outputs']
    after = iam.get_role(RoleName=role_name)['Role']
    assert fingerprint(after['AssumeRolePolicyDocument']) == trust_digest
    policy = iam.get_role_policy(RoleName=role_name, PolicyName=POLICY_NAME)['PolicyDocument']
    expected = {'Version':'2012-10-17','Statement':[
        {'Effect':'Allow','Action':['execute-api:Invoke'],'Resource':exact_invoke_arn(target.account,api)},
        {'Effect':'Deny','Action':['lambda:InvokeFunction'],'Resource':'*'}]}
    assert policy == expected
    proof.update(policy_readback='EXACT_MATCH', trust_readback='UNCHANGED', other_resources='UNCHANGED',
                 target_after=target.verify())
    # IAM simulation is labeled as simulation, never actual workload evidence.
    app_resources = cf.list_stack_resources(StackName=PROJECT+'-serverless-app')
    assert not app_resources.get('NextToken')
    function = next(r['PhysicalResourceId'] for r in app_resources['StackResourceSummaries'] if r['LogicalResourceId']=='FoundationExchange')
    function_arn = target.client('lambda').get_function_configuration(FunctionName=function)['FunctionArn']
    sim = iam.simulate_principal_policy(PolicySourceArn=role['Arn'], ActionNames=['lambda:InvokeFunction'], ResourceArns=[function_arn])
    assert not sim.get('IsTruncated')
    decisions = [r['EvalDecision'] for r in sim['EvaluationResults']]
    proof['direct_lambda_simulation'] = decisions
    assert decisions == ['explicitDeny']
    sim = iam.simulate_principal_policy(PolicySourceArn=role['Arn'], ActionNames=['execute-api:Invoke'], ResourceArns=[exact_invoke_arn(target.account,api)])
    assert not sim.get('IsTruncated')
    proof['exact_route_simulation'] = [r['EvalDecision'] for r in sim['EvaluationResults']]
    assert proof['exact_route_simulation'] == ['allowed']
    proof['status'] = 'VERIFIED_POLICY_ONLY'
    save(proof)
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','execute'])
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.action), indent=2))
    except Exception as exc:
        if RECEIPT.exists():
            proof = json.loads(RECEIPT.read_text())
            proof['error'] = sanitized(exc)
            save(proof)
        print(sanitized(exc))
        raise SystemExit(1) from None
