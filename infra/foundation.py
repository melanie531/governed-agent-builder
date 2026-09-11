"""Isolated M0 resources. No shared identity, network or existing Gateway writes.

Model inference is quarantined until its Policy action/schema is proven.
This template intentionally grants the Model Gateway NO Bedrock inference access.
"""
from pathlib import Path

MODEL_NAME = 'gab-foundation-model-m0'
TOOLS_NAME = 'gab-foundation-tools-m0'
ROLE_NAME = 'gab-foundation-m0-runtime'
MODEL_ID = 'anthropic.claude-haiku-4-5'


def sub(value):
    return {'Fn::Sub': value}


def arn(resource, attribute='Arn'):
    return {'Fn::GetAtt': [resource, attribute]}


def statement(actions, resource, effect='Allow'):
    return {'Effect': effect, 'Action': actions, 'Resource': resource}


def role(name, source, statements, service='bedrock-agentcore.amazonaws.com'):
    return {'Type': 'AWS::IAM::Role', 'Properties': {
        'RoleName': name,
        'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole',
            'Condition': {'StringEquals': {'aws:SourceAccount': {'Ref': 'AWS::AccountId'}},
                          'ArnLike': {'aws:SourceArn': sub(source)}}}]},
        'Policies': [{'PolicyName': 'owned-foundation-only', 'PolicyDocument': {
            'Version': '2012-10-17', 'Statement': statements}}]}}


def template():
    r = {}
    r['PolicyEngine'] = {'Type': 'AWS::BedrockAgentCore::PolicyEngine',
                         'Properties': {'Name': 'gab_foundation_m0', 'Description': 'Owned M0 only'}}
    engine = arn('PolicyEngine', 'PolicyEngineArn')
    for label, name in (('Model', MODEL_NAME), ('Tools', TOOLS_NAME)):
        gateway_scope = sub('arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:gateway/' + name + '-*')
        statements = [
            statement(['bedrock-agentcore:GetPolicyEngine'], engine),
            statement(['bedrock-agentcore:AuthorizeAction', 'bedrock-agentcore:PartiallyAuthorizeActions'],
                      [engine, gateway_scope])]
        if label == 'Tools':
            statements.append(statement(['lambda:InvokeFunction'], arn('Fixture')))
        else:
            statements.append(statement(['bedrock-mantle:ListModels'],
                sub('arn:${AWS::Partition}:bedrock-mantle:${AWS::Region}:${AWS::AccountId}:project/default')))
            statements.append(statement(['bedrock:*', 'bedrock-mantle:CreateInference'], '*', 'Deny'))
        r[label + 'Role'] = role('gab-foundation-m0-' + label.lower(),
            'arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:gateway/' + name + '-*',
            statements)
        r[label + 'Gateway'] = {'Type': 'AWS::BedrockAgentCore::Gateway', 'Properties': {
            'Name': name, 'RoleArn': arn(label + 'Role'), 'AuthorizerType': 'AWS_IAM',
            'ProtocolType': 'MCP', 'ProtocolConfiguration': {'Mcp': {'SupportedVersions': ['2025-03-26']}},
            'PolicyEngineConfiguration': {'Arn': engine, 'Mode': 'ENFORCE'},
            'Description': 'Owned M0; production disabled; model policy unverified' if label == 'Model'
                           else 'Owned M0 synthetic fixture; not Browser acceptance'}}
    r['ModelTarget'] = {'Type': 'AWS::BedrockAgentCore::GatewayTarget', 'Properties': {
        'GatewayIdentifier': {'Ref': 'ModelGateway'}, 'Name': 'claude',
        'CredentialProviderConfigurations': [{'CredentialProviderType': 'GATEWAY_IAM_ROLE'}],
        'TargetConfiguration': {'Inference': {'Provider': {
            'Endpoint': 'https://bedrock-mantle.us-west-2.api.aws',
            'ModelMapping': {'ProviderPrefix': {'Strip': True, 'Separator': '.'}},
            'Operations': [{'Path': '/v1/messages', 'ProviderPath': '/anthropic/v1/messages',
                            'Models': [{'Model': MODEL_ID}]}]}}}}}
    r['FixtureRole'] = {'Type': 'AWS::IAM::Role', 'Properties': {
        'RoleName': 'gab-foundation-m0-fixture',
        'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}}}
    # No execution permissions: pure synthetic data, no network SDK, logs or store.
    r['Fixture'] = {'Type': 'AWS::Lambda::Function', 'Properties': {
        'FunctionName': 'gab-foundation-m0-fixture', 'Runtime': 'python3.13',
        'Handler': 'index.handler', 'Role': arn('FixtureRole'), 'Timeout': 3, 'MemorySize': 128,
        'Architectures': ['arm64'], 'ReservedConcurrentExecutions': 1,
        'Code': {'ZipFile': (Path(__file__).resolve().parents[1] / 'tools/synthetic_fixture.py').read_text()}}}
    r['ToolsTarget'] = {'Type': 'AWS::BedrockAgentCore::GatewayTarget', 'Properties': {
        'GatewayIdentifier': {'Ref': 'ToolsGateway'}, 'Name': 'fixture',
        'CredentialProviderConfigurations': [{'CredentialProviderType': 'GATEWAY_IAM_ROLE'}],
        'TargetConfiguration': {'Mcp': {'Lambda': {'LambdaArn': arn('Fixture'),
            'ToolSchema': {'InlinePayload': [{
                'Name': 'lookup', 'Description': 'Read one synthetic sample; no Browser or external traffic.',
                'InputSchema': {'Type': 'object', 'Properties': {'key': {'Type': 'string'}}, 'Required': ['key']}}]}}}}}}
    r['FoundationRole'] = role(ROLE_NAME,
        'arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:runtime/gab_foundation_*', [
            statement(['bedrock-agentcore:InvokeGateway'], [arn('ModelGateway', 'GatewayArn'), arn('ToolsGateway', 'GatewayArn')]),
            statement(['xray:PutTraceSegments'], '*'),
            statement(['bedrock:*', 'bedrock-mantle:*', 'bedrock-agentcore:StartBrowserSession',
                       'bedrock-agentcore:ConnectBrowserAutomationStream',
                       'bedrock-agentcore:InvokeAgentRuntimeCommand'], '*', 'Deny')])
    # Strict validation of an exact TOOL action; does not claim model coverage.
    cedar = ('permit(principal is AgentCore::IamEntity, '
             'action == AgentCore::Action::"fixture___lookup", '
             'resource == AgentCore::Gateway::"${ToolsGateway.GatewayArn}") '
             'when { principal.id == "${FoundationRole.Arn}" || '
             'principal.id like "arn:${AWS::Partition}:sts::${AWS::AccountId}:assumed-role/'
             + ROLE_NAME + '/*" };')
    r['ToolPolicy'] = {'Type': 'AWS::BedrockAgentCore::Policy', 'DependsOn': ['ToolsTarget'],
                      'Properties': {'PolicyEngineId': arn('PolicyEngine', 'PolicyEngineId'),
                                     'Name': 'synthetic_lookup', 'ValidationMode': 'FAIL_ON_ANY_FINDINGS',
                                     'EnforcementMode': 'ACTIVE', 'Definition': {'Cedar': {'Statement': sub(cedar)}}}}
    r['Evidence'] = {'Type': 'AWS::S3::Bucket', 'DeletionPolicy': 'Retain',
                     'UpdateReplacePolicy': 'Retain', 'Properties': {
        'BucketEncryption': {'ServerSideEncryptionConfiguration': [{'ServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]},
        'OwnershipControls': {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]},
        'PublicAccessBlockConfiguration': {'BlockPublicAcls': True, 'IgnorePublicAcls': True,
                                          'BlockPublicPolicy': True, 'RestrictPublicBuckets': True},
        'VersioningConfiguration': {'Status': 'Enabled'},
        'LifecycleConfiguration': {'Rules': [
            {'Id': 'review-content', 'Status': 'Enabled', 'Prefix': 'proof/',
             'ExpirationInDays': 6, 'NoncurrentVersionExpiration': {'NoncurrentDays': 1},
             'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}},
            {'Id': 'expired-markers', 'Status': 'Enabled', 'Prefix': 'proof/',
             'ExpiredObjectDeleteMarker': True}]}}}
    r['EvidenceTLS'] = {'Type': 'AWS::S3::BucketPolicy', 'Properties': {
        'Bucket': {'Ref': 'Evidence'}, 'PolicyDocument': {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*',
            'Resource': [arn('Evidence'), sub('${Evidence.Arn}/*')],
            'Condition': {'Bool': {'aws:SecureTransport': 'false'}}}]}}}
    r['Operations'] = {'Type': 'AWS::Logs::LogGroup', 'DeletionPolicy': 'Retain', 'Properties': {
        'LogGroupName': '/governed-agent-builder/foundation-m0', 'RetentionInDays': 7}}
    return {'AWSTemplateFormatVersion': '2010-09-09',
            'Description': 'Owned generic foundation M0; no Runtime/network/identity mutation; inference quarantined',
            'Resources': r, 'Outputs': {
                'ModelGateway': {'Value': arn('ModelGateway', 'GatewayArn')},
                'ToolsGateway': {'Value': arn('ToolsGateway', 'GatewayArn')},
                'FoundationRole': {'Value': arn('FoundationRole')},
                'EvidenceBucket': {'Value': {'Ref': 'Evidence'}},
                'ModelPolicyCoverage': {'Value': 'BLOCKED_INFERENCE_SCHEMA_UNVERIFIED'}}}


def admission_statement(api_id):
    """Attach only to an independently reviewed dedicated immutable package role."""
    import re
    if not re.fullmatch(r'[a-z0-9]+', api_id):
        raise ValueError('EXACT_ADMISSION_API_REQUIRED')
    return statement(['execute-api:Invoke'], sub(
        'arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:'
        + api_id + '/$default/POST/internal/foundation/exchange'))
