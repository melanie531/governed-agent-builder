"""Add only the dedicated ALPR IAM exchange to an existing app template."""
import copy
import re

from infra.serverless import attr, ref, sub


def configure(resources, *, account, region, runtimes, roles):
    runtimes, roles = sorted(set(runtimes)), sorted(set(roles))
    previous = resources.get('JourneyALPRExchangeRole')
    if previous:
        statements = previous['Properties']['Policies'][0]['PolicyDocument']['Statement']
        runtimes = sorted(set(runtimes) | {r for s in statements if 'bedrock-agentcore:GetAgentRuntime' in s.get('Action', [])
                                         for r in s['Resource'] if '/runtime-endpoint/' not in r})
        roles = sorted(set(roles) | {r for s in statements if 'iam:GetRole' in s.get('Action', []) for r in s['Resource']})
    if (not re.fullmatch(r'\d{12}', account) or region != 'us-west-2' or not runtimes or not roles
            or any(not re.fullmatch(rf'arn:aws:bedrock-agentcore:{region}:{account}:runtime/[A-Za-z0-9_-]+', arn) for arn in runtimes)
            or any(not re.fullmatch(rf'arn:aws:iam::{account}:role/[^/]+', role) for role in roles)):
        raise ValueError('Exact ALPR workload targets required')
    # Preserve existing resource definitions, roles, model grants, optional
    # foundations and specialist targets. Clone only the existing exchange slice.
    for suffix in ('Logs', 'Role', '', 'Integration', 'Route', 'Permission'):
        source = resources['FoundationExchange' + suffix]
        def rename(value):
            if isinstance(value, dict): return {key: rename(val) for key, val in value.items()}
            if isinstance(value, list): return [rename(val) for val in value]
            if isinstance(value, str):
                return value.replace('FoundationExchange', 'JourneyALPRExchange').replace(
                    'foundation-exchange', 'journey-alpr-exchange').replace('internal/foundation/exchange', 'internal/journey/alpr')
            return value
        resources['JourneyALPRExchange' + suffix] = rename(copy.deepcopy(source))
    function = resources['JourneyALPRExchange']['Properties']
    function['Handler'] = 'backend.serverless.journey_alpr_exchange_handler'
    function['Timeout'] = 29
    source_env = resources['Business']['Properties']['Environment']['Variables']
    function['Environment']['Variables'] = {
        key: source_env[key] for key in ('STATE_TABLE', 'PUBLIC_URL', 'COGNITO_REGION', 'COGNITO_USER_POOL_ID',
                                       'COGNITO_CLIENT_ID', 'COGNITO_DOMAIN')}
    function['Environment']['Variables'].update(JOURNEY_ALPR_EXCHANGE_ENABLED='1', JOURNEY_ALPR_API_ID=ref('Api'))
    resources['JourneyALPRExchangeIntegration']['Properties']['TimeoutInMillis'] = 29000
    statements = resources['JourneyALPRExchangeRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    statements.extend([
        {'Effect': 'Allow', 'Action': ['bedrock-agentcore:GetAgentRuntime', 'bedrock-agentcore:ListAgentRuntimeVersions',
            'bedrock-agentcore:GetAgentRuntimeEndpoint', 'bedrock-agentcore:GetResourcePolicy'],
         'Resource': [resource for arn in runtimes for resource in (arn, arn + '/runtime-endpoint/DEFAULT')]},
        {'Effect': 'Allow', 'Action': ['iam:GetRole', 'iam:ListRolePolicies', 'iam:GetRolePolicy', 'iam:ListAttachedRolePolicies'],
         'Resource': list(roles)}])
    # Even a same-account identity policy must not allow Runtime/browser callers
    # to directly invoke Lambda with forged API Gateway requestContext.
    for name in ('BusinessRole', 'WorkerRole'):
        policy = {'PolicyName': 'ALPRExchangeDirectInvokeDenied', 'PolicyDocument': {'Version': '2012-10-17',
            'Statement': [{'Effect': 'Deny', 'Action': 'lambda:InvokeFunction', 'Resource': attr('JourneyALPRExchange')}]}}
        policies = resources[name]['Properties']['Policies']
        policies[:] = [p for p in policies if p['PolicyName'] != policy['PolicyName']] + [policy]
