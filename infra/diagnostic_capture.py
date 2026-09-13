"""Additive diagnostic state service; never regenerates the deployed app stack.

The peer supplies the fresh deployed template and immutable backend artifact.
Runtime creation, role isolation and reviewed capture authority are separate gates.
"""
import copy
import re

ROUTE = 'POST /internal/diagnostic/capture'
LOGICAL_IDS = frozenset({'DiagnosticCaptureLogs', 'DiagnosticCaptureRole',
    'DiagnosticCaptureExchange', 'DiagnosticCaptureIntegration',
    'DiagnosticCaptureRoute', 'DiagnosticCapturePermission'})


def sub(value):
    return {'Fn::Sub': value}


def attr(name):
    return {'Fn::GetAtt': [name, 'Arn']}


def route_arn(api_id):
    if not re.fullmatch('[a-z0-9]+', api_id):
        raise ValueError('EXACT_API_REQUIRED')
    return sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:'
               + api_id + '/$default/POST/internal/diagnostic/capture')


def runtime_isolation_policy(api_id, function_arn):
    """Additional restrictions ONLY; review/remove inherited allows separately."""
    if not re.fullmatch(r'arn:aws:lambda:us-west-2:\d{12}:function:[A-Za-z0-9_-]+', function_arn):
        raise ValueError('EXACT_FUNCTION_REQUIRED')
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['execute-api:Invoke'], 'Resource': route_arn(api_id)},
        {'Effect': 'Deny', 'Action': ['dynamodb:*', 'sts:AssumeRole', 'iam:PassRole'], 'Resource': '*'},
        {'Effect': 'Deny', 'Action': ['lambda:InvokeFunction'],
         'Resource': [function_arn, function_arn + ':*']}]}


def assemble(current, *, bucket, key, version, runtime_arn, endpoint_arn):
    if (not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket)
            or not re.fullmatch(r'approved/[A-Za-z0-9/_.-]+\.zip', key)
            or '..' in key.split('/') or not version or version == 'null'):
        raise ValueError('IMMUTABLE_APPROVED_BACKEND_ARTIFACT_REQUIRED')
    if not re.fullmatch(r'arn:aws:bedrock-agentcore:us-west-2:\d{12}:runtime/gab_foundation_[A-Za-z0-9_-]+', runtime_arn):
        raise ValueError('EXACT_OWNED_RUNTIME_REQUIRED')
    if not endpoint_arn.startswith(runtime_arn + '/runtime-endpoint/') or not re.fullmatch(
            '[A-Za-z0-9_-]+', endpoint_arn.rsplit('/', 1)[-1]):
        raise ValueError('EXACT_RUNTIME_ENDPOINT_REQUIRED')
    result = copy.deepcopy(current)
    r = result['Resources']
    if LOGICAL_IDS & r.keys():
        raise ValueError('DIAGNOSTIC_RESOURCES_ALREADY_PRESENT_REVIEW_UPDATE_SEPARATELY')
    for name, kind in {'Api': 'AWS::ApiGatewayV2::Api', 'State': 'AWS::DynamoDB::Table',
                        'Stage': 'AWS::ApiGatewayV2::Stage',
                        'FoundationExchange': 'AWS::Lambda::Function'}.items():
        if r.get(name, {}).get('Type') != kind:
            raise ValueError('EXISTING_STUDIO_TEMPLATE_REQUIRED')
    if r['Stage']['Properties'].get('StageName') != '$default':
        raise ValueError('EXACT_DEFAULT_STAGE_REQUIRED')
    if any(x.get('Type') == 'AWS::ApiGatewayV2::Route' and
           x['Properties'].get('RouteKey') == ROUTE for x in r.values()):
        raise ValueError('ROUTE_ALREADY_EXISTS')
    table = attr('State')
    def ddb(actions, keys):
        return {'Effect': 'Allow', 'Action': actions, 'Resource': table,
                'Condition': {'ForAllValues:StringEquals': {'dynamodb:LeadingKeys': keys}}}
    statements = [
        ddb(['dynamodb:GetItem', 'dynamodb:Query'], ['_revision', 'settings', 'agents', 'versions', 'principals', 'audit']),
        ddb(['dynamodb:ConditionCheckItem'], ['_revision']),
        ddb(['dynamodb:PutItem', 'dynamodb:UpdateItem'], ['_revision', 'settings']),
        {'Effect': 'Allow', 'Action': ['bedrock-agentcore:GetAgentRuntime'], 'Resource': runtime_arn},
        {'Effect': 'Allow', 'Action': ['bedrock-agentcore:GetAgentRuntimeEndpoint'], 'Resource': endpoint_arn},
        {'Effect': 'Allow', 'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'],
         'Resource': attr('DiagnosticCaptureLogs')},
        {'Effect': 'Deny', 'Action': ['bedrock:*', 'bedrock-mantle:*',
            'bedrock-agentcore:InvokeAgentRuntime', 'bedrock-agentcore:InvokeGateway',
            'sts:AssumeRole', 'iam:PassRole'], 'Resource': '*'}]
    r['DiagnosticCaptureLogs'] = {'Type': 'AWS::Logs::LogGroup', 'DeletionPolicy': 'Retain',
        'Properties': {'LogGroupName': '/governed-agent-builder/diagnostic-capture', 'RetentionInDays': 1}}
    r['DiagnosticCaptureRole'] = {'Type': 'AWS::IAM::Role', 'Properties': {
        'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]},
        'Policies': [{'PolicyName': 'DiagnosticCaptureStateOnly', 'PolicyDocument': {
            'Version': '2012-10-17', 'Statement': statements}}]}}
    r['DiagnosticCaptureExchange'] = {'Type': 'AWS::Lambda::Function', 'Properties': {
        'Runtime': 'python3.13', 'Architectures': ['arm64'],
        'Handler': 'backend.serverless.diagnostic_capture_exchange_handler',
        'Role': attr('DiagnosticCaptureRole'), 'MemorySize': 512, 'Timeout': 15,
        'ReservedConcurrentExecutions': 1,
        'Code': {'S3Bucket': bucket, 'S3Key': key, 'S3ObjectVersion': version},
        'Environment': {'Variables': {'STATE_TABLE': {'Ref': 'State'},
            'DIAGNOSTIC_CAPTURE_API_ID': {'Ref': 'Api'}, 'DIAGNOSTIC_CAPTURE_EXCHANGE_ENABLED': '0'}},
        'LoggingConfig': {'LogGroup': {'Ref': 'DiagnosticCaptureLogs'}}}}
    r['DiagnosticCaptureIntegration'] = {'Type': 'AWS::ApiGatewayV2::Integration', 'Properties': {
        'ApiId': {'Ref': 'Api'}, 'IntegrationType': 'AWS_PROXY', 'IntegrationMethod': 'POST',
        'IntegrationUri': attr('DiagnosticCaptureExchange'), 'PayloadFormatVersion': '2.0', 'TimeoutInMillis': 15000}}
    r['DiagnosticCaptureRoute'] = {'Type': 'AWS::ApiGatewayV2::Route', 'Properties': {
        'ApiId': {'Ref': 'Api'}, 'RouteKey': ROUTE, 'AuthorizationType': 'AWS_IAM',
        'Target': {'Fn::Join': ['/', ['integrations', {'Ref': 'DiagnosticCaptureIntegration'}]]}}}
    r['DiagnosticCapturePermission'] = {'Type': 'AWS::Lambda::Permission', 'Properties': {
        'FunctionName': {'Ref': 'DiagnosticCaptureExchange'}, 'Action': 'lambda:InvokeFunction',
        'Principal': 'apigateway.amazonaws.com', 'SourceAccount': {'Ref': 'AWS::AccountId'},
        'SourceArn': sub('arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/$default/POST/internal/diagnostic/capture')}}
    # No existing properties, parameters, outputs, role policies or code keys change.
    assert all(result['Resources'][k] == v for k, v in current['Resources'].items())
    return result
