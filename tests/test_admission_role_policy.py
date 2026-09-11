"""Offline policy boundary tests, never a workload authentication receipt."""
from copy import deepcopy
import pytest
from infra.foundation import template
from scripts.admission_role_policy import overlay, inspect_change_set, exact_invoke_arn, POLICY_NAME


def test_overlay_changes_only_role_policies_and_keeps_trust_and_denies():
    old = template()
    new = overlay(old, 'syntheticapi')
    assert old == template()
    assert new['Outputs'] == old['Outputs']
    for key in old['Resources']:
        if key != 'FoundationRole':
            assert new['Resources'][key] == old['Resources'][key]
    before = old['Resources']['FoundationRole']['Properties']
    after = new['Resources']['FoundationRole']['Properties']
    assert {k:v for k,v in before.items() if k != 'Policies'} == {k:v for k,v in after.items() if k != 'Policies'}
    assert after['Policies'][:-1] == before['Policies']
    added = after['Policies'][-1]
    assert added['PolicyName'] == POLICY_NAME
    allow, deny = added['PolicyDocument']['Statement']
    assert allow['Action'] == ['execute-api:Invoke']
    assert allow['Resource']['Fn::Sub'].endswith(':syntheticapi/$default/POST/internal/foundation/exchange')
    assert '*' not in str(allow)
    assert deny == {'Effect':'Deny', 'Action':['lambda:InvokeFunction'], 'Resource':'*'}
    assert overlay(new, 'syntheticapi') == new
    with pytest.raises(ValueError):
        overlay(new, 'anotherapi')


@pytest.mark.parametrize('api', ['*', 'abc/other', '', 'abc?query', 'abc:*'])
def test_no_wildcard_or_path_injection(api):
    with pytest.raises(ValueError):
        overlay(template(), api)


def test_no_human_trust():
    body = template()
    body['Resources']['FoundationRole']['Properties']['AssumeRolePolicyDocument']['Statement'][0]['Principal'] = {'AWS':'*'}
    with pytest.raises(ValueError, match='TRUST'):
        overlay(body, 'syntheticapi')


def change():
    return {'StackName':'synthetic-stack','Status':'CREATE_COMPLETE','ExecutionStatus':'AVAILABLE',
            'Changes':[{'ResourceChange':{'LogicalResourceId':'FoundationRole','ResourceType':'AWS::IAM::Role',
                'Action':'Modify','Replacement':'False','Details':[{'Target':{'Attribute':'Properties','Name':'Policies','RequiresRecreation':'Never'}}]}}]}


def test_exact_nonreplacement_policy_modify_only():
    assert inspect_change_set(change(), 'synthetic-stack')['replacement'] is False
    for key, value in [('LogicalResourceId','OtherRole'),('Action','Add'),('Replacement','True')]:
        body = change()
        body['Changes'][0]['ResourceChange'][key] = value
        with pytest.raises(ValueError):
            inspect_change_set(body, 'synthetic-stack')
    body = change()
    body['Changes'][0]['ResourceChange']['Details'][0]['Target']['Name'] = 'AssumeRolePolicyDocument'
    with pytest.raises(ValueError):
        inspect_change_set(body, 'synthetic-stack')
    body = change()
    body['Changes'].append(deepcopy(body['Changes'][0]))
    with pytest.raises(ValueError):
        inspect_change_set(body, 'synthetic-stack')


def test_exact_resolved_arn():
    account = '9988' + '77665544'
    assert exact_invoke_arn(account, 'syntheticapi') == f'arn:aws:execute-api:us-west-2:{account}:syntheticapi/$default/POST/internal/foundation/exchange'
    with pytest.raises(ValueError):
        exact_invoke_arn('*', 'syntheticapi')


def dependency_change():
    body = change()
    body['Changes'].append({'ResourceChange': {'LogicalResourceId':'ToolPolicy',
        'ResourceType':'AWS::BedrockAgentCore::Policy', 'Action':'Modify', 'Replacement':'False',
        'Scope':['Properties'], 'Details':[{'Target':{'Attribute':'Properties', 'Name':'Definition',
        'RequiresRecreation':'Never'}, 'ChangeSource':'ResourceAttribute',
        'CausingEntity':'FoundationRole.Arn', 'Evaluation':'Dynamic'}]}})
    return body


def test_dependency_requires_exact_evidence_and_detail():
    evidence = dict(resolved_cedar_unchanged=True, role_arn_unchanged=True, principal_action_resource_unchanged=True)
    with pytest.raises(ValueError):
        inspect_change_set(dependency_change(), 'synthetic-stack')
    assert not inspect_change_set(dependency_change(), 'synthetic-stack', dependency_evidence=evidence)['replacement']
    for field, value in [('CausingEntity','OtherRole.Arn'), ('ChangeSource','DirectModification'), ('Evaluation','Static')]:
        body=dependency_change();body['Changes'][1]['ResourceChange']['Details'][0][field]=value
        with pytest.raises(ValueError):
            inspect_change_set(body,'synthetic-stack',dependency_evidence=evidence)
    for field, value in [('Action','Remove'),('Replacement','True')]:
        body=dependency_change();body['Changes'][1]['ResourceChange'][field]=value
        with pytest.raises(ValueError):
            inspect_change_set(body,'synthetic-stack',dependency_evidence=evidence)


def test_cedar_change_or_unrelated_template_change_denied():
    from scripts.admission_role_policy import verify_dependency
    old=template(); new=overlay(old,'syntheticapi'); account='9988'+'77665544'
    role=f'arn:aws:iam::{account}:role/'+old['Resources']['FoundationRole']['Properties']['RoleName']
    gateway='synthetic-gateway'
    cedar=old['Resources']['ToolPolicy']['Properties']['Definition']['Cedar']['Statement']['Fn::Sub']
    for key,value in {'FoundationRole.Arn':role,'ToolsGateway.GatewayArn':gateway,'AWS::Partition':'aws','AWS::AccountId':account}.items():
        cedar=cedar.replace('${'+key+'}',value)
    live={'status':'ACTIVE','definition':{'cedar':{'statement':cedar}}}
    assert verify_dependency(old,new,role,gateway,account,live)['resolved_cedar_unchanged']
    live['definition']['cedar']['statement'] += 'permit(principal,action,resource);'
    with pytest.raises(ValueError):verify_dependency(old,new,role,gateway,account,live)
    new['Resources']['ToolPolicy']['Properties']['EnforcementMode']='LOG_ONLY'
    with pytest.raises(ValueError):verify_dependency(old,new,role,gateway,account,live)
