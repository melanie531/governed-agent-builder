"""Fail-closed live IAM/Cedar resolution review; receipts contain only digests."""
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def resolve(value, attrs, pseudo):
    if isinstance(value, list):
        return [resolve(v, attrs, pseudo) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {'Ref'}:
        return pseudo[value['Ref']]
    if set(value) == {'Fn::GetAtt'}:
        return attrs['.'.join(value['Fn::GetAtt'])]
    if set(value) == {'Fn::Sub'}:
        text = value['Fn::Sub']
        if not isinstance(text, str):
            raise ValueError('UNSUPPORTED_SUBSTITUTION')
        return re.sub(r'\$\{([^}]+)\}', lambda m: (attrs | pseudo)[m[1]], text)
    if any(k.startswith('Fn::') for k in value):
        raise ValueError('UNSUPPORTED_INTRINSIC')
    return {k: resolve(v, attrs, pseudo) for k, v in value.items()}


def verify_live(target, template, ids):
    """Compare whole resolved IAM policies/trust, actual Gateway bindings and Cedar."""
    iam = target.client('iam')
    control = target.client('bedrock-agentcore-control')
    resources = template['Resources']
    roles = {}; gateways = {}; attrs = {}
    for name, resource in resources.items():
        kind = resource['Type']
        if kind == 'AWS::IAM::Role':
            role = iam.get_role(RoleName=ids[name])['Role']
            roles[name] = role
            attrs[name + '.Arn'] = role['Arn']
        elif kind == 'AWS::BedrockAgentCore::Gateway':
            gw = control.get_gateway(gatewayIdentifier=ids[name])
            if gw['gatewayId'] != ids[name] or gw['status'] != 'READY':
                raise ValueError('GATEWAY_ID_OR_STATE_MISMATCH')
            gateways[name] = gw
            attrs[name + '.GatewayArn'] = gw['gatewayArn']
        elif kind == 'AWS::Lambda::Function':
            lam = target.client('lambda').get_function_configuration(FunctionName=ids[name])
            attrs[name + '.Arn'] = lam['FunctionArn']
    engine = control.get_policy_engine(policyEngineId=ids['PolicyEngine'].split('/')[-1])
    attrs['PolicyEngine.PolicyEngineArn'] = engine['policyEngineArn']
    attrs['PolicyEngine.PolicyEngineId'] = engine['policyEngineId']
    pseudo = {'AWS::Partition': 'aws', 'AWS::Region': 'us-west-2', 'AWS::AccountId': target.account}
    receipt = {}
    for name, role in roles.items():
        expected = resolve(resources[name]['Properties'], attrs, pseudo)
        if role['RoleName'] != expected['RoleName'] or role['AssumeRolePolicyDocument'] != expected['AssumeRolePolicyDocument']:
            raise ValueError('LIVE_ROLE_TRUST_MISMATCH')
        if role.get('PermissionsBoundary') or expected.get('ManagedPolicyArns'):
            raise ValueError('UNREVIEWED_ROLE_BOUNDARY')
        managed = iam.list_attached_role_policies(RoleName=ids[name])
        listed = iam.list_role_policies(RoleName=ids[name])
        if managed.get('IsTruncated') or managed['AttachedPolicies'] or listed.get('IsTruncated'):
            raise ValueError('UNREVIEWED_MANAGED_POLICY')
        policies = {p['PolicyName']: p['PolicyDocument'] for p in expected.get('Policies', [])}
        if set(listed['PolicyNames']) != set(policies):
            raise ValueError('LIVE_INLINE_POLICY_SET_MISMATCH')
        for policy_name, policy in policies.items():
            actual = iam.get_role_policy(RoleName=ids[name], PolicyName=policy_name)['PolicyDocument']
            if actual != policy:
                raise ValueError('LIVE_RESOLVED_IAM_MISMATCH')
        receipt[name] = digest({'arn': role['Arn'], 'trust': role['AssumeRolePolicyDocument'], 'policies': policies})
    for name, gw in gateways.items():
        expected = resolve(resources[name]['Properties'], attrs, pseudo)
        for prop, field in [('Name','name'), ('RoleArn','roleArn'), ('AuthorizerType','authorizerType'), ('ProtocolType','protocolType')]:
            if expected[prop] != gw[field]:
                raise ValueError('LIVE_GATEWAY_BINDING_MISMATCH')
        if gw['policyEngineConfiguration'] != {'arn': expected['PolicyEngineConfiguration']['Arn'], 'mode': 'ENFORCE'} or expected['PolicyEngineConfiguration']['Mode'] != 'ENFORCE':
            raise ValueError('ENFORCE_ATTACHMENT_MISMATCH')
        expected_config = [{'interceptionPoints': c['InterceptionPoints'],
            'interceptor': {'lambda': {'arn': c['Interceptor']['Lambda']['Arn']}},
            'inputConfiguration': {'passRequestHeaders': c['InputConfiguration']['PassRequestHeaders']}}
            for c in expected.get('InterceptorConfigurations', [])]
        if gw.get('interceptorConfigurations', []) != expected_config:
            raise ValueError('EXACT_INTERCEPTOR_MISMATCH')
        receipt[name] = digest({k: gw[k] for k in ('gatewayArn', 'gatewayId', 'roleArn', 'authorizerType', 'policyEngineConfiguration')})
    expected = resolve(resources['ToolPolicy']['Properties'], attrs, pseudo)
    policy = control.get_policy(policyEngineId=engine['policyEngineId'], policyId=ids['ToolPolicy'].split('/')[-1])
    if (policy['definition'] != {'cedar': {'statement': expected['Definition']['Cedar']['Statement']}}
            or policy['name'] != expected['Name'] or policy['enforcementMode'] != expected['EnforcementMode']
            or policy['status'] != 'ACTIVE'):
        raise ValueError('LIVE_RESOLVED_CEDAR_MISMATCH')
    receipt['ToolPolicy'] = digest({'definition': policy['definition'], 'mode': policy['enforcementMode'], 'id': ids['ToolPolicy']})
    receipt['physical_ids_digest'] = digest(ids)
    return receipt
