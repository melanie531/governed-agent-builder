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

def test_template_changes_only_business_leadingkeys_logout_tags_and_bundled_fonts():
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
    csp = expected['Resources']['Headers']['Properties']['ResponseHeadersPolicyConfig']['SecurityHeadersConfig']['ContentSecurityPolicy']
    csp['ContentSecurityPolicy'] = csp['ContentSecurityPolicy'].replace(
        "img-src 'self' data:;", "img-src 'self' data:; font-src 'self' data:;")
    # Account-global CloudFront resource names carry the region suffix so a
    # second-region installation in the same account cannot collide.
    expected['Resources']['OAC']['Properties']['OriginAccessControlConfig']['Name'] = {
        'Fn::Sub': '${AWS::StackName}-s3-${AWS::Region}'}
    expected['Resources']['SPA']['Properties']['Name'] = {
        'Fn::Sub': '${AWS::StackName}-spa-${AWS::Region}'}
    expected['Resources']['Headers']['Properties']['ResponseHeadersPolicyConfig']['Name'] = {
        'Fn::Sub': '${AWS::StackName}-headers-${AWS::Region}'}
    # Retention tagging is the only additional deployment-wide change. Its
    # supported types and required values have independent contract coverage.
    for name, resource in new['Resources'].items():
        field = 'UserPoolTags' if resource['Type'] == 'AWS::Cognito::UserPool' else 'Tags'
        actual = resource['Properties'].get(field)
        previous = expected['Resources'][name]['Properties'].get(field)
        if actual != previous:
            if isinstance(actual, dict):
                assert actual == {**(previous or {}), 'auto-delete': 'no'}
            else:
                assert actual == [*(previous or []), {'Key': 'auto-delete', 'Value': 'no'}]
            expected['Resources'][name]['Properties'][field] = actual
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
