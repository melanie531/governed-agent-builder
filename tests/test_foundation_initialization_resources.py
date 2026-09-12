"""Synthetic control-plane readbacks; no deployed resource mutation."""
import copy
from unittest.mock import Mock

import pytest
from scripts.initialize_foundation import Resources, APP, ARTIFACTS, STACK
from tests.test_foundation_initialization import ACCOUNT, ROLE, WORKER


@pytest.fixture
def metadata(monkeypatch):
    session = Mock()
    clients = {n: Mock() for n in ('sts', 'cloudformation', 'iam', 'lambda', 'dynamodb', 's3', 'apigatewayv2', 'ec2')}
    session.client.side_effect = lambda name, **kwargs: clients[name]
    clients['sts'].get_caller_identity.return_value = {'Account': ACCOUNT}
    target = Mock(account=ACCOUNT)
    monkeypatch.setattr('scripts.initialize_foundation.StudioTarget', lambda s: target)
    resource_specs = {
        APP: [('State', 'DynamoDB::Table', 'synthetic-state'), ('WorkerRole', 'IAM::Role', 'synthetic-worker'),
              ('Worker', 'Lambda::Function', 'synthetic-worker-function'), ('Business', 'Lambda::Function', 'synthetic-business'),
              ('Api', 'ApiGatewayV2::Api', 'syntheticapi'),
              ('FoundationExchangeRoute', 'ApiGatewayV2::Route', 'synthetic-route'),
              ('FoundationExchange', 'Lambda::Function', 'synthetic-exchange')],
        ARTIFACTS: [('Releases', 'S3::Bucket', 'synthetic-artifacts')],
        STACK: [('FoundationRole', 'IAM::Role', 'synthetic-runtime')]}
    outputs = {
        APP: {'StateTable': 'synthetic-state', 'WorkerFunction': 'synthetic-worker-function',
              'ApiEndpoint': 'https://syntheticapi.execute-api.us-west-2.amazonaws.com'},
        ARTIFACTS: {'Bucket': 'synthetic-artifacts'},
        STACK: {'FoundationRole': ROLE, 'FoundationSubnets': 'subnet-aaaa', 'FoundationSecurityGroups': 'sg-aaaa'}}
    stacks = {name: {'StackId': f'arn:aws:cloudformation:us-west-2:{ACCOUNT}:stack/{name}/synthetic',
                   'StackStatus': 'CREATE_COMPLETE', 'Tags': [{'Key': 'project', 'Value': 'governed-agent-builder'}],
                   'Outputs': [{'OutputKey': k, 'OutputValue': v} for k, v in values.items()],
                   'Parameters': [{'ParameterKey': 'ArtifactBucket', 'ParameterValue': 'synthetic-artifacts'}]}
              for name, values in outputs.items()}
    clients['cloudformation'].describe_stacks.side_effect = lambda StackName: {'Stacks': [stacks[StackName]]}
    clients['cloudformation'].list_stack_resources.side_effect = lambda StackName: {'StackResourceSummaries': [
        {'LogicalResourceId': logical, 'PhysicalResourceId': physical, 'ResourceType': 'AWS::' + kind,
         'ResourceStatus': 'CREATE_COMPLETE'} for logical, kind, physical in resource_specs[StackName.split('/')[1]]]}
    def get_role(RoleName):
        runtime = RoleName == 'synthetic-runtime'
        return {'Role': {'Arn': ROLE if runtime else WORKER, 'RoleId': 'synthetic-' + RoleName,
            'AssumeRolePolicyDocument': {'Statement': [{'Effect': 'Allow', 'Action': 'sts:AssumeRole',
                'Principal': {'Service': 'bedrock-agentcore.amazonaws.com' if runtime else 'lambda.amazonaws.com'},
                'Condition': {'StringEquals': {'aws:SourceAccount': ACCOUNT}, 'ArnLike': {
                    'aws:SourceArn': f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:runtime/gab_foundation_*'}}}]}}}
    clients['iam'].get_role.side_effect = get_role
    clients['iam'].simulate_principal_policy.side_effect = lambda **kw: {'EvaluationResults': [
        {'EvalActionName': action, 'EvalDecision': 'allowed'} for action in kw['ActionNames']]}
    clients['lambda'].get_function_configuration.return_value = {'Role': WORKER}
    clients['dynamodb'].describe_table.return_value = {'Table': {
        'TableArn': f'arn:aws:dynamodb:us-west-2:{ACCOUNT}:table/synthetic-state', 'TableStatus': 'ACTIVE',
        'KeySchema': [{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
        'AttributeDefinitions': [{'AttributeName': k, 'AttributeType': 'S'} for k in ('pk', 'sk')]}}
    clients['apigatewayv2'].get_route.return_value = {'RouteKey': 'POST /internal/foundation/exchange',
        'AuthorizationType': 'AWS_IAM', 'Target': 'integrations/synthetic'}
    clients['apigatewayv2'].get_integration.return_value = {
        'IntegrationUri': f'arn:aws:lambda:us-west-2:{ACCOUNT}:function:synthetic-exchange'}
    s3 = clients['s3']
    s3.get_bucket_location.return_value = {'LocationConstraint': 'us-west-2'}
    s3.get_bucket_versioning.return_value = {'Status': 'Enabled'}
    s3.get_public_access_block.return_value = {'PublicAccessBlockConfiguration': {k: True for k in (
        'BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')}}
    s3.get_bucket_policy_status.return_value = {'PolicyStatus': {'IsPublic': False}}
    s3.get_bucket_encryption.return_value = {'ServerSideEncryptionConfiguration': {'Rules': [
        {'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}}
    import json
    s3.get_bucket_policy.return_value = {'Policy': json.dumps({'Statement': [{'Effect': 'Deny',
        'Principal': '*', 'Action': 's3:*', 'Resource': ['arn:aws:s3:::synthetic-artifacts', 'arn:aws:s3:::synthetic-artifacts/*'],
        'Condition': {'Bool': {'aws:SecureTransport': 'false'}}}]})}
    s3.list_objects_v2.return_value = {'Contents': [{'Key': 'not-approval.zip', 'Size': 100}], 'IsTruncated': False}
    clients['ec2'].describe_subnets.return_value = {'Subnets': [{'SubnetId': 'subnet-aaaa', 'VpcId': 'vpc-aaaa',
        'OwnerId': ACCOUNT, 'State': 'available', 'MapPublicIpOnLaunch': False}]}
    clients['ec2'].describe_security_groups.return_value = {'SecurityGroups': [{'GroupId': 'sg-aaaa', 'VpcId': 'vpc-aaaa', 'OwnerId': ACCOUNT}]}
    return Resources(session, ACCOUNT, 'us-west-2', APP), clients, stacks


def test_complete_output_adapter_is_read_only(metadata):
    resources, clients, _ = metadata
    facts, config, issues = resources.collect()
    assert not issues
    assert config['roles'] == [ROLE] and config['producer_role'] == WORKER
    assert facts['artifact_inventory']['sample_count'] == 1
    assert facts['artifact_inventory']['approval_inferred'] is False
    assert facts['exchange_endpoint'].endswith('/internal/foundation/exchange')
    for client in clients.values():
        assert all(call[0].startswith(('get_', 'list_', 'describe_', 'simulate_')) for call in client.mock_calls)


@pytest.mark.parametrize('bad', ['stack', 'worker', 'ddb-key', 'live-flag', 'public', 'versioning', 'endpoint', 'network'])
def test_invalid_resource_fact_is_blocked(metadata, bad):
    resources, clients, stacks = metadata
    if bad == 'stack':
        stacks[STACK]['StackId'] = stacks[STACK]['StackId'].replace(ACCOUNT, '8877' '66554433')
    if bad == 'worker':
        clients['lambda'].get_function_configuration.return_value['Role'] = ROLE
    if bad == 'ddb-key':
        clients['dynamodb'].describe_table.return_value['Table']['KeySchema'].pop()
    if bad == 'live-flag':
        clients['lambda'].get_function_configuration.return_value['Environment'] = {'Variables': {'FOUNDATION_LIVE_ENABLED': '1'}}
    if bad == 'public':
        clients['s3'].get_bucket_policy_status.return_value['PolicyStatus']['IsPublic'] = True
    if bad == 'versioning':
        clients['s3'].get_bucket_versioning.return_value['Status'] = 'Suspended'
    if bad == 'endpoint':
        clients['apigatewayv2'].get_route.return_value['AuthorizationType'] = 'NONE'
    if bad == 'network':
        clients['ec2'].describe_subnets.return_value['Subnets'][0]['MapPublicIpOnLaunch'] = True
    with pytest.raises(ValueError):
        resources.collect()


def test_missing_network_and_permissions_still_produce_verified_facts(metadata):
    resources, clients, stacks = metadata
    stacks[STACK]['Outputs'] = [o for o in stacks[STACK]['Outputs'] if o['OutputKey'] == 'FoundationRole']
    clients['iam'].simulate_principal_policy.side_effect = lambda **kw: {'EvaluationResults': [
        {'EvalActionName': action, 'EvalDecision': 'implicitDeny'} for action in kw['ActionNames']]}
    facts, config, issues = resources.collect()
    assert config is None and facts['runtime_role']['arn'] == ROLE
    assert any(i.startswith('EXISTING_VPC_OUTPUTS_REQUIRED') for i in issues)
    assert 'WORKER_PASSROLE_PERMISSION_REQUIRED' in issues
    clients['ec2'].describe_subnets.assert_not_called()
