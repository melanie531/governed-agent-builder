"""Narrow admission IAM overlay; preserves live trust and every other resource.

This is not workload authentication proof. No Runtime is created by this module.
"""
from copy import deepcopy
import re

from infra.foundation import admission_statement

POLICY_NAME = 'foundation-admission-exchange-only'


def overlay(current, api_id):
    desired = deepcopy(current)
    resource = desired['Resources']['FoundationRole']
    if resource['Type'] != 'AWS::IAM::Role':
        raise ValueError('FOUNDATION_ROLE_REQUIRED')
    props = resource['Properties']
    trust = props['AssumeRolePolicyDocument']['Statement']
    if (len(trust) != 1 or trust[0].get('Effect') != 'Allow'
            or trust[0].get('Principal') != {'Service': 'bedrock-agentcore.amazonaws.com'}
            or trust[0].get('Action') != 'sts:AssumeRole'
            or not trust[0].get('Condition')):
        raise ValueError('EXISTING_AGENTCORE_ONLY_TRUST_REQUIRED')
    policy = {'PolicyName': POLICY_NAME, 'PolicyDocument': {
        'Version': '2012-10-17', 'Statement': [admission_statement(api_id),
            {'Effect': 'Deny', 'Action': ['lambda:InvokeFunction'], 'Resource': '*'}]}}
    policies = props.setdefault('Policies', [])
    matches = [p for p in policies if p.get('PolicyName') == POLICY_NAME]
    if matches:
        if matches != [policy]:
            raise ValueError('EXISTING_ADMISSION_POLICY_MISMATCH')
    else:
        policies.append(policy)
    return desired


def inspect_change_set(response, stack_name, *, dependency_evidence=None):
    if (response.get('StackName') != stack_name
            or response.get('Status') != 'CREATE_COMPLETE'
            or response.get('ExecutionStatus') != 'AVAILABLE'
            or response.get('NextToken')):
        raise ValueError('CHANGESET_NOT_EXECUTABLE')
    changes = response.get('Changes', [])
    if len(changes) == 2 and dependency_evidence == {
            'resolved_cedar_unchanged': True, 'role_arn_unchanged': True,
            'principal_action_resource_unchanged': True}:
        extra = [c['ResourceChange'] for c in changes if c['ResourceChange'].get('LogicalResourceId') == 'ToolPolicy']
        if len(extra) != 1:
            raise ValueError('EXACT_TOOL_DEPENDENCY_REQUIRED')
        extra = extra[0]
        if (extra.get('ResourceType') != 'AWS::BedrockAgentCore::Policy'
                or extra.get('Action') != 'Modify' or extra.get('Replacement') != 'False'
                or extra.get('Scope') != ['Properties'] or not extra.get('Details')):
            raise ValueError('EXACT_TOOL_DEPENDENCY_REQUIRED')
        for detail in extra['Details']:
            target = detail.get('Target', {})
            if (target.get('Attribute') != 'Properties' or target.get('Name') != 'Definition'
                    or target.get('RequiresRecreation') != 'Never'
                    or detail.get('ChangeSource') != 'ResourceAttribute'
                    or detail.get('CausingEntity') != 'FoundationRole.Arn'
                    or detail.get('Evaluation') != 'Dynamic'):
                raise ValueError('EXACT_TOOL_DEPENDENCY_REQUIRED')
        changes = [c for c in changes if c['ResourceChange'].get('LogicalResourceId') != 'ToolPolicy']
    if len(changes) != 1:
        raise ValueError('FOUNDATION_ROLE_ONLY')
    change = changes[0]['ResourceChange']
    if (change.get('LogicalResourceId') != 'FoundationRole'
            or change.get('ResourceType') != 'AWS::IAM::Role'
            or change.get('Action') != 'Modify'
            or change.get('Replacement') != 'False'):
        raise ValueError('ONLY_NONREPLACING_ROLE_MODIFY')
    if not change.get('Details'):
        raise ValueError('ROLE_PROPERTY_DETAILS_REQUIRED')
    for detail in change.get('Details', []):
        target = detail.get('Target', {})
        if (target.get('Attribute') != 'Properties' or target.get('Name') != 'Policies'
                or target.get('RequiresRecreation') != 'Never'):
            raise ValueError('ONLY_ROLE_POLICIES_CHANGE')
    return {'logical_id': 'FoundationRole', 'action': 'Modify', 'replacement': False}


def exact_invoke_arn(account, api_id):
    if not re.fullmatch(r'\d{12}', account):
        raise ValueError('EXACT_ACCOUNT_REQUIRED')
    # Reuse the source validator before resolving pseudo parameters.
    admission_statement(api_id)
    return f'arn:aws:execute-api:us-west-2:{account}:{api_id}/$default/POST/internal/foundation/exchange'


def verify_dependency(current, desired, role_arn, gateway_arn, account, live_policy):
    """Prove byte-identical resolved Cedar, not merely matching change labels."""
    if current['Resources']['ToolPolicy'] != desired['Resources']['ToolPolicy']:
        raise ValueError('TOOL_POLICY_TEMPLATE_CHANGED')
    before = deepcopy(current)
    before['Resources']['FoundationRole']['Properties']['Policies'] = desired['Resources']['FoundationRole']['Properties']['Policies']
    if before != desired:
        raise ValueError('UNRELATED_TEMPLATE_CHANGE')
    if role_arn != f'arn:aws:iam::{account}:role/' + current['Resources']['FoundationRole']['Properties']['RoleName']:
        raise ValueError('ROLE_IDENTITY_CHANGED')
    statement = current['Resources']['ToolPolicy']['Properties']['Definition']['Cedar']['Statement']['Fn::Sub']
    for key, value in {'FoundationRole.Arn': role_arn, 'ToolsGateway.GatewayArn': gateway_arn,
                       'AWS::Partition': 'aws', 'AWS::AccountId': account}.items():
        statement = statement.replace('${'+key+'}', value)
    if '${' in statement or live_policy['definition'] != {'cedar': {'statement': statement}}:
        raise ValueError('RESOLVED_POLICY_SEMANTICS_CHANGED')
    if live_policy.get('status') != 'ACTIVE':
        raise ValueError('LIVE_POLICY_NOT_ACTIVE')
    return {'resolved_cedar_unchanged': True, 'role_arn_unchanged': True,
            'principal_action_resource_unchanged': True}
