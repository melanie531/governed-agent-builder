"""Least-privilege regression: Business entity keys and exact logout routing."""
import copy
import subprocess
from infra.serverless import template

BASE='db7c6056b1417ef7650c27dff5b7cbdb8533857e'

def baseline():
    source=subprocess.check_output(['git','show',BASE+':infra/serverless.py'],text=True)
    namespace={'__name__':'infra.serverless_baseline','__package__':'infra'}
    exec(compile(source,'baseline/serverless.py','exec'),namespace)
    return namespace['template']()

def test_template_changes_only_business_leadingkeys_and_scoped_logout():
    old=baseline();new=template();expected=copy.deepcopy(old);changed=0
    for policy in expected['Resources']['BusinessRole']['Properties']['Policies']:
        for st in policy['PolicyDocument']['Statement']:
            keys=st.get('Condition',{}).get('ForAllValues:StringEquals',{}).get('dynamodb:LeadingKeys')
            if keys is not None:
                assert 'general_requests' not in keys
                st['Condition']['ForAllValues:StringEquals']['dynamodb:LeadingKeys']=[*keys,'general_requests'];changed+=1
    assert changed==2
    # Logout must remain reachable after the session authorizer denies access.
    # Enumerate the exact additions while retaining equality for every existing
    # resource, especially IAM roles and protected business routes.
    expected['Resources']['Route8'] = {
        'Type': 'AWS::ApiGatewayV2::Route',
        'Properties': {
            'ApiId': {'Ref': 'Api'}, 'RouteKey': 'POST /api/auth/logout',
            'AuthorizationType': 'NONE',
            'Target': {'Fn::Join': ['/', ['integrations', {'Ref': 'AuthIntegration'}]]},
        },
    }
    expected['Resources']['AuthPermission6'] = {
        'Type': 'AWS::Lambda::Permission',
        'Properties': {
            'FunctionName': {'Ref': 'Auth'}, 'Action': 'lambda:InvokeFunction',
            'Principal': 'apigateway.amazonaws.com', 'SourceAccount': {'Ref': 'AWS::AccountId'},
            'SourceArn': {'Fn::Sub': 'arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/*/POST/api/auth/logout'},
        },
    }
    assert new==expected

def test_other_roles_and_unapproved_partitions_remain_ungranted():
    doc=template()
    for name,resource in doc['Resources'].items():
        if resource.get('Type')!='AWS::IAM::Role':continue
        for policy in resource['Properties'].get('Policies',[]):
            for st in policy['PolicyDocument']['Statement']:
                keys=st.get('Condition',{}).get('ForAllValues:StringEquals',{}).get('dynamodb:LeadingKeys')
                if keys is None:continue
                assert '*' not in keys
                assert 'unapproved_partition' not in keys
                if name!='BusinessRole':assert 'general_requests' not in keys
