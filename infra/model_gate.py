"""Overlay only the owned Model Gateway; never regenerate the live stack."""
from copy import deepcopy
from pathlib import Path

from infra.foundation import arn, sub, statement, MODEL_NAME

ADDED = {'ModelGate', 'ModelGateRole', 'ModelGateLogs'}
MODIFIED = {'ModelRole', 'ModelGateway'}


def overlay(live):
    result = deepcopy(live)
    r = result['Resources']
    if ADDED & r.keys():
        raise ValueError('ALREADY_PRESENT_REVIEW_REQUIRED')
    gateway = r['ModelGateway']['Properties']
    if (gateway['Name'] != MODEL_NAME or gateway['AuthorizerType'] != 'AWS_IAM'
            or gateway['PolicyEngineConfiguration']['Mode'] != 'ENFORCE'
            or gateway.get('InterceptorConfigurations')):
        raise ValueError('OWNED_GATEWAY_REQUIRED')
    policies = r['ModelRole']['Properties']['Policies']
    statements = policies[0]['PolicyDocument']['Statement']
    if not any(s['Effect'] == 'Deny' and 'bedrock-mantle:CreateInference' in s['Action']
               and s['Resource'] == '*' for s in statements):
        raise ValueError('INFERENCE_QUARANTINE_REQUIRED')
    r['ModelGateLogs'] = {'Type': 'AWS::Logs::LogGroup', 'Properties': {
        'LogGroupName': '/aws/lambda/gab-foundation-m0-model-gate', 'RetentionInDays': 7}}
    r['ModelGateRole'] = {'Type': 'AWS::IAM::Role', 'Properties': {
        'RoleName': 'gab-foundation-m0-model-gate',
        'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]},
        'Policies': [{'PolicyName': 'exact-gate-logs', 'PolicyDocument': {
            'Version': '2012-10-17', 'Statement': [statement(
                ['logs:CreateLogStream', 'logs:PutLogEvents'],
                sub('arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/gab-foundation-m0-model-gate:*'))]}}]}}
    r['ModelGate'] = {'Type': 'AWS::Lambda::Function', 'DependsOn': ['ModelGateLogs'], 'Properties': {
        'FunctionName': 'gab-foundation-m0-model-gate', 'Runtime': 'python3.13', 'Handler': 'index.handler',
        'Role': arn('ModelGateRole'), 'Timeout': 3, 'MemorySize': 128,
        'Architectures': ['arm64'], 'Code': {'ZipFile': (
            Path(__file__).resolve().parents[1] / 'tools/model_gate/handler.py').read_text()}}}
    statements.append(statement(['lambda:InvokeFunction'], arn('ModelGate')))
    gateway['InterceptorConfigurations'] = [{
        'InterceptionPoints': ['REQUEST'], 'Interceptor': {'Lambda': {'Arn': arn('ModelGate')}},
        'InputConfiguration': {'PassRequestHeaders': False}}]
    return result


def inspect_change_set(change):
    if change.get('Status') != 'CREATE_COMPLETE' or change.get('ExecutionStatus') != 'AVAILABLE' or change.get('NextToken'):
        raise ValueError('CHANGESET_NOT_READY')
    seen = set()
    for entry in change.get('Changes', []):
        c = entry['ResourceChange']
        name = c['LogicalResourceId']
        if name in seen:
            raise ValueError('DUPLICATE_CHANGE')
        seen.add(name)
        if name in ADDED:
            if c['Action'] != 'Add':
                raise ValueError('ADD_ONLY')
        elif name in MODIFIED:
            if c['Action'] != 'Modify' or c.get('Replacement') != 'False':
                raise ValueError('NO_REPLACEMENT')
            allowed = {'Policies'} if name == 'ModelRole' else {'InterceptorConfigurations', 'RoleArn'}
            for d in c.get('Details', []):
                t = d['Target']
                if t.get('Attribute') != 'Properties' or t.get('Name') not in allowed or t.get('RequiresRecreation') != 'Never':
                    raise ValueError('UNEXPECTED_PROPERTY')
        else:
            # Even harmless dependencies outside this exact set need review.
            raise ValueError('UNREVIEWED_DEPENDENCY')
    if seen != ADDED | MODIFIED:
        raise ValueError('CHANGESET_SCOPE_MISMATCH')
    return 'EXACT_NONREPLACING_MODEL_GATE_DELTA'
