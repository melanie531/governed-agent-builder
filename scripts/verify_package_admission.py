"""Metadata-only platform verification. Never reads user passwords or SSM.

A reviewed immutable approval is required from the platform's protected store;
there is deliberately no CLI endpoint/role/authority override.
"""
import json
import re
import time
from foundation_harness.config import digest, load_config
from foundation_harness.package_admission import admission_config, validate_admission
from scripts.foundation_target import StudioTarget, PROJECT, STACK


def verify_package_admission(raw, *, approved=None, target=None):
    load_config(raw, digest(raw))
    # A syntactically valid manifest is not an approved agent/version binding.
    if (not isinstance(approved, dict) or approved.get('config') != raw
            or approved.get('manifest_digest') != digest(raw)
            or approved.get('runtime_admission_reviewed') is not True
            or not approved.get('definition_digest')):
        raise ValueError('PLATFORM_OWNED_APPROVED_MANIFEST_REQUIRED')
    target = target or StudioTarget()
    target.verify()
    if read_platform_approval(approved['definition_digest'], target=target) != approved:
        raise ValueError('PROTECTED_APPROVAL_READBACK_REQUIRED')
    if approved.get('expires_at', 0) <= time.time():
        raise ValueError('PLATFORM_APPROVAL_EXPIRED')
    cf = target.client('cloudformation')
    app = cf.describe_stacks(StackName=PROJECT+'-serverless-app')['Stacks'][0]
    outputs = {o['OutputKey']: o['OutputValue'] for o in app['Outputs']}
    endpoint = outputs['ApiEndpoint'].rstrip('/')+'/internal/foundation/exchange'
    api = endpoint.split('//')[1].split('.')[0]
    routes = target.client('apigatewayv2').get_routes(ApiId=api)
    route = [r for r in routes['Items'] if r['RouteKey'] == 'POST /internal/foundation/exchange']
    if routes.get('NextToken') or len(route) != 1 or route[0]['AuthorizationType'] != 'AWS_IAM':
        raise ValueError('EXACT_IAM_EXCHANGE_REQUIRED')
    resources = cf.list_stack_resources(StackName=STACK)
    roles = [r['PhysicalResourceId'] for r in resources['StackResourceSummaries'] if r['LogicalResourceId'] == 'FoundationRole']
    if resources.get('NextToken') or len(roles) != 1:
        raise ValueError('EXACT_PLATFORM_ROLE_REQUIRED')
    role = target.client('iam').get_role(RoleName=roles[0])['Role']
    trust = role['AssumeRolePolicyDocument']['Statement']
    if (len(trust) != 1 or trust[0]['Principal'] != {'Service': 'bedrock-agentcore.amazonaws.com'}
            or trust[0]['Action'] != 'sts:AssumeRole' or trust[0]['Effect'] != 'Allow'
            or not trust[0].get('Condition') or role['Arn'] != approved.get('role')
            or role['Arn'].split(':')[4] != target.account):
        raise ValueError('EXACT_PLATFORM_ROLE_REQUIRED')
    settings = admission_config(raw, endpoint, role['Arn'])
    if validate_admission(approved.get('admission'), raw) != settings:
        raise ValueError('PLATFORM_ENDPOINT_BINDING_MISMATCH')
    return settings


def read_platform_approval(definition_digest, *, target=None):
    """One exact settings GetItem; never scan users/sessions/passwords."""
    if not isinstance(definition_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', definition_digest):
        raise ValueError('EXACT_DEFINITION_DIGEST_REQUIRED')
    target = target or StudioTarget()
    target.verify()
    resources = target.client('cloudformation').list_stack_resources(StackName=PROJECT+'-serverless-app')
    tables = [r['PhysicalResourceId'] for r in resources['StackResourceSummaries'] if r['LogicalResourceId'] == 'State']
    if resources.get('NextToken') or len(tables) != 1:
        raise ValueError('EXACT_PLATFORM_STORE_REQUIRED')
    key = 'foundation-approved:'+definition_digest
    result = target.client('dynamodb').get_item(TableName=tables[0], ConsistentRead=True,
        Key={'pk': {'S':'settings'}, 'sk': {'S':json.dumps([key], separators=(',', ':'))}})
    if 'Item' not in result:
        raise ValueError('PLATFORM_OWNED_APPROVED_MANIFEST_REQUIRED')
    row = json.loads(result['Item']['body']['S'])
    if row.get('key') != key:
        raise ValueError('PLATFORM_APPROVAL_KEY_MISMATCH')
    approved = json.loads(row['body'])
    receipt = approved.get('receipt') or {}
    if (receipt.get('definition_digest') != definition_digest or receipt.get('approver_role') != 'admin'
            or not receipt.get('approver') or not receipt.get('request_id') or not receipt.get('reviewed_at')
            or not approved.get('foundation_id')):
        raise ValueError('AUTHENTICATED_APPROVAL_RECEIPT_REQUIRED')
    def setting(key):
        response = target.client('dynamodb').get_item(TableName=tables[0], ConsistentRead=True,
            Key={'pk': {'S':'settings'}, 'sk': {'S':json.dumps([key], separators=(',', ':'))}})
        item = response.get('Item')
        return json.loads(json.loads(item['body']['S'])['body']) if item else None
    # Read-only packaging is not runtime authority; execution checks the CAS ledger
    # again. Never package a known revoked grant or superseded registered source.
    policy = setting('policy') or {}
    source = setting('foundation-source:'+approved['foundation_id']) or {}
    if (approved.get('epoch') != (setting('foundation-epoch') or 0)
            or approved.get('policy_version') != policy.get('version')
            or approved.get('source_revision') != source.get('revision')
            or receipt != setting('foundation-review:'+receipt['request_id'])):
        raise ValueError('CURRENT_REVIEWED_APPROVAL_REQUIRED')
    return approved
