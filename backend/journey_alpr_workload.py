"""Concrete AWS proof for a protected, immutable ALPR deployment record.

Role identity is useful only with exclusive Runtime trust, exclusive version use,
immutable artifacts, and backend/Gateway-only invocation. No caller assertion is
accepted here. Only the operator registration command writes deployment records.
"""
import json
import re

from foundation_harness.config import digest
from .foundation_runs import get
from .journey_alpr import require

PREFIX = 'journey-alpr-deployment:'
CONFIGURATION = ('roleArn', 'agentRuntimeArtifact', 'networkConfiguration', 'protocolConfiguration',
                 'environmentVariables', 'authorizerConfiguration', 'requestHeaderConfiguration',
                 'lifecycleConfiguration')
ACTIONS = {'execute-api:Invoke', 'bedrock-agentcore:InvokeGateway', 'bedrock:InvokeModel',
           's3:GetObject', 's3:GetObjectVersion', 's3:PutObject', 'logs:CreateLogGroup',
           'logs:CreateLogStream', 'logs:DescribeLogStreams', 'logs:PutLogEvents',
           'ecr:GetAuthorizationToken', 'ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer',
           'ssm:GetParameter', 'kms:Decrypt', 'xray:PutTraceSegments', 'xray:PutTelemetryRecords'}


def trust_policy(arn):
    return {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow',
        'Principal': {'Service': 'bedrock-agentcore.amazonaws.com'}, 'Action': 'sts:AssumeRole',
        'Condition': {'StringEquals': {'aws:SourceAccount': arn.split(':')[4]},
                      'ArnLike': {'aws:SourceArn': arn}}}]}


def invocation_policy(arn, caller):
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Principal': {'AWS': caller}, 'Action': 'bedrock-agentcore:InvokeAgentRuntime', 'Resource': arn},
        {'Effect': 'Deny', 'Principal': '*', 'Action': 'bedrock-agentcore:InvokeAgentRuntime', 'Resource': arn,
         'Condition': {'ArnNotEquals': {'aws:PrincipalArn': caller}}},
        {'Effect': 'Deny', 'Principal': '*', 'Action': 'bedrock-agentcore:InvokeAgentRuntimeCommand*', 'Resource': arn}]}


def configuration(value):
    return {key: value[key] for key in CONFIGURATION if key in value}


def role_from_principal(principal):
    match = re.fullmatch(r'arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+', principal or '')
    require(match, 'ALPR_AUTHENTICATED_ROLE_SESSION_REQUIRED')
    return f'arn:aws:iam::{match[1]}:role/{match[2]}'


def verify_runtime(control, iam, row, caller, *, purpose='a'):
    arn, version = row['arn'], row['version']
    require(re.fullmatch(r'arn:aws:bedrock-agentcore:us-west-2:\d{12}:runtime/[A-Za-z0-9_-]+', arn)
            and row['id'] == arn.rsplit('/', 1)[1] and re.fullmatch(r'[1-9][0-9]*', version),
            'ALPR_RUNTIME_RECORD_DENIED')
    role = row['configuration']['roleArn']
    require(re.fullmatch(r'arn:aws:iam::' + arn.split(':')[4] + r':role/[^/]+', role), 'ALPR_DEDICATED_ROLE_REQUIRED')
    latest = control.get_agent_runtime(agentRuntimeId=row['id'])
    require(latest.get('status') == 'READY' and latest.get('agentRuntimeArn') == arn
            and latest.get('agentRuntimeVersion') == version and configuration(latest) == row['configuration']
            and not latest.get('authorizerConfiguration'), 'ALPR_RUNTIME_CONFIGURATION_CHANGED')
    artifact = row['configuration']['agentRuntimeArtifact']
    image = artifact.get('containerConfiguration', {}).get('containerUri', '')
    code = artifact.get('codeConfiguration', {}).get('code', {}).get('s3', {})
    require(bool(re.fullmatch(r'\d{12}\.dkr\.ecr\.us-west-2\.amazonaws\.com/[^@]+@sha256:[a-f0-9]{64}', image))
            or bool(code.get('bucket') and code.get('prefix') and code.get('versionId') not in (None, '', 'null')),
            'ALPR_IMMUTABLE_ARTIFACT_REQUIRED')
    # Reject role reuse in *any* earlier version, not just the DEFAULT endpoint.
    token, seen = None, set()
    for _ in range(20):
        page = control.list_agent_runtime_versions(agentRuntimeId=row['id'], maxResults=100,
                                                  **({'nextToken': token} if token else {}))
        for item in page['agentRuntimes']:
            prior = item['agentRuntimeVersion']
            require(prior not in seen, 'ALPR_VERSION_ENUMERATION_DENIED')
            seen.add(prior)
            if prior != version:
                old = control.get_agent_runtime(agentRuntimeId=row['id'], agentRuntimeVersion=prior)
                require(old.get('roleArn') != role, 'ALPR_ROLE_REUSED_BY_VERSION')
        token = page.get('nextToken')
        if not token:
            break
    require(not token and version in seen, 'ALPR_VERSION_ENUMERATION_DENIED')
    endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=row['id'], endpointName='DEFAULT')
    require(endpoint.get('status') == 'READY' and endpoint.get('agentRuntimeArn') == arn
            and endpoint.get('liveVersion') == version and endpoint.get('targetVersion', version) == version
            and endpoint.get('agentRuntimeEndpointArn') == row['endpoint_arn'], 'ALPR_ENDPOINT_CHANGED')
    for resource in (arn, row['endpoint_arn']):
        policy = json.loads(control.get_resource_policy(resourceArn=resource)['policy'])
        require(policy == invocation_policy(resource, caller), 'ALPR_EXCLUSIVE_INGRESS_REQUIRED')
    name = role.rsplit('/', 1)[1]
    actual = iam.get_role(RoleName=name)['Role']
    require(actual['Arn'] == role and actual['AssumeRolePolicyDocument'] == trust_policy(arn),
            'ALPR_EXCLUSIVE_RUNTIME_TRUST_REQUIRED')
    attached = iam.list_attached_role_policies(RoleName=name)
    require(not attached.get('AttachedPolicies') and not attached.get('IsTruncated'), 'ALPR_MANAGED_ROLE_POLICY_DENIED')
    policies = iam.list_role_policies(RoleName=name)
    require(policies.get('PolicyNames') and not policies.get('IsTruncated'), 'ALPR_ROLE_POLICY_REQUIRED')
    for name_policy in policies['PolicyNames']:
        policy = iam.get_role_policy(RoleName=name, PolicyName=name_policy)['PolicyDocument']
        for statement in policy['Statement']:
            actions = statement.get('Action', [])
            actions = [actions] if isinstance(actions, str) else actions
            allowed = ACTIONS - ({'ssm:GetParameter', 'kms:Decrypt'} if purpose == 'a' else {
                'bedrock:InvokeModel', 'bedrock-agentcore:InvokeGateway', 's3:GetObject',
                's3:GetObjectVersion', 's3:PutObject'})
            require(statement.get('Effect') == 'Allow' and actions and set(actions) <= allowed
                    and not set(statement) - {'Sid', 'Effect', 'Action', 'Resource', 'Condition'}, 'ALPR_ROLE_PRIVILEGE_DENIED')
            resources = statement.get('Resource', [])
            resources = [resources] if isinstance(resources, str) else resources
            for resource in resources:
                wildcard_allowed = (
                    set(actions) <= {'ecr:GetAuthorizationToken', 'xray:PutTraceSegments', 'xray:PutTelemetryRecords'}
                    or set(actions) <= {'logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:DescribeLogStreams', 'logs:PutLogEvents'}
                    and resource.startswith(f'arn:aws:logs:us-west-2:{arn.split(":")[4]}:log-group:')
                    or set(actions) == {'s3:PutObject'} and re.fullmatch(
                        r'arn:aws:s3:::[a-z0-9.-]+/journey/evidence/[a-f0-9]{64}/\*', resource))
                require('?' not in resource and ('*' not in resource or wildcard_allowed), 'ALPR_ROLE_RESOURCE_SCOPE_DENIED')
                if 'ssm:GetParameter' in actions:
                    require(resource in [f'arn:aws:ssm:us-west-2:{arn.split(":")[4]}:parameter/governed-agent-builder/alpr/{key}'
                                         for key in ('account', 'user', 'private-key')], 'ALPR_SSM_SCOPE_DENIED')
            require(resources, 'ALPR_ROLE_RESOURCE_SCOPE_DENIED')


def load(db, reference):
    require(isinstance(reference, str) and re.fullmatch(r'[a-f0-9]{64}', reference), 'ALPR_DEPLOYMENT_REQUIRED')
    record = get(db, PREFIX + reference)
    require(record and digest(record) == reference and not get(db, PREFIX + 'revoked:' + reference),
            'ALPR_DEPLOYMENT_REVOKED_OR_CHANGED')
    require(not get(db, 'journey-alpr-listing:revoked:' + record['specialist']['deployment_digest']),
            'ALPR_SPECIALIST_DEPLOYMENT_REVOKED')
    return record


def specialist_binding(runtime):
    identity = {'runtime_arn': runtime['arn'], 'runtime_version': runtime['version'],
                'workload': runtime['configuration']['roleArn']}
    return {**identity, 'deployment_digest': digest({
        **identity, 'artifact': runtime['configuration']['agentRuntimeArtifact']})}


def verify(control, iam, record):
    require(record['a']['configuration']['roleArn'] != record['b']['configuration']['roleArn'], 'ALPR_ROLES_MUST_DIFFER')
    require(record['specialist'] == specialist_binding(record['b']), 'ALPR_SPECIALIST_RECORD_DENIED')
    verify_runtime(control, iam, record['a'], record['backend_role'])
    verify_runtime(control, iam, record['b'], record['gateway_role'], purpose='b')
