"""Offline workload binding; fake only AWS, keep repository and admission real."""
from copy import deepcopy
from types import SimpleNamespace
import json
import pytest

from backend.journey_alpr_workload import verify_runtime, trust_policy, invocation_policy
from foundation_harness.context import Denied

ARN = 'arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/dedicated-A'
ROLE = 'arn:aws:iam::123456789012:role/dedicated-A'
CALLER = 'arn:aws:iam::123456789012:role/backend'


def aws_runtime():
    configuration = {'roleArn': ROLE, 'agentRuntimeArtifact': {'containerConfiguration': {
        'containerUri': '123456789012.dkr.ecr.us-west-2.amazonaws.com/alpr@sha256:' + 'a' * 64}},
        'environmentVariables': {}, 'networkConfiguration': {'networkMode': 'PUBLIC'},
        'protocolConfiguration': {'serverProtocol': 'HTTP'}}
    row = {'id': 'dedicated-A', 'arn': ARN, 'version': '2', 'configuration': configuration,
           'endpoint_arn': ARN + '/runtime-endpoint/DEFAULT'}
    old = {'agentRuntimeArn': ARN, 'agentRuntimeVersion': '1', 'roleArn': ROLE + '-bootstrap'}
    current = {'agentRuntimeArn': ARN, 'agentRuntimeVersion': '2', 'status': 'READY', **configuration}
    control = SimpleNamespace(
        get_agent_runtime=lambda **kw: deepcopy(old if kw.get('agentRuntimeVersion') == '1' else current),
        list_agent_runtime_versions=lambda **kw: {'agentRuntimes': [{'agentRuntimeVersion': '1'}, {'agentRuntimeVersion': '2'}]},
        get_agent_runtime_endpoint=lambda **kw: {'status': 'READY', 'agentRuntimeArn': ARN,
            'liveVersion': '2', 'agentRuntimeEndpointArn': row['endpoint_arn']},
        get_resource_policy=lambda **kw: {'policy': json.dumps(invocation_policy(kw['resourceArn'], CALLER))})
    iam = SimpleNamespace(get_role=lambda **kw: {'Role': {'Arn': ROLE, 'AssumeRolePolicyDocument': trust_policy(ARN)}},
        list_attached_role_policies=lambda **kw: {'AttachedPolicies': []},
        list_role_policies=lambda **kw: {'PolicyNames': ['scope']},
        get_role_policy=lambda **kw: {'PolicyDocument': {'Version': '2012-10-17', 'Statement': [
            {'Effect': 'Allow', 'Action': ['execute-api:Invoke'], 'Resource': 'arn:aws:execute-api:us-west-2:123456789012:api/$default/POST/internal/journey/alpr'}]}})
    row['role_policies'] = {'scope': iam.get_role_policy()['PolicyDocument']}
    return row, control, iam, old, current


def test_exact_immutable_runtime_with_exclusive_role():
    row, control, iam, _, _ = aws_runtime()
    verify_runtime(control, iam, row, CALLER)


@pytest.mark.parametrize('drift', ['version', 'prior-role', 'trust', 'policy', 'endpoint', 'artifact', 'managed', 'iam-write', 'role-scope'])
def test_workload_drift_denied(drift):
    row, control, iam, old, current = aws_runtime()
    if drift == 'version': current['agentRuntimeVersion'] = '3'
    if drift == 'prior-role': old['roleArn'] = ROLE
    if drift == 'trust': iam.get_role = lambda **kw: {'Role': {'Arn': ROLE, 'AssumeRolePolicyDocument': trust_policy(ARN + '*')}}
    if drift == 'policy': control.get_resource_policy = lambda **kw: {'policy': '{"Statement": []}'}
    if drift == 'endpoint': control.get_agent_runtime_endpoint = lambda **kw: {'status': 'READY', 'liveVersion': '1'}
    if drift == 'artifact': current['agentRuntimeArtifact'] = {'containerConfiguration': {'containerUri': 'mutable:latest'}}
    if drift == 'managed': iam.list_attached_role_policies = lambda **kw: {'AttachedPolicies': [{'PolicyArn': 'admin'}]}
    if drift == 'iam-write': iam.get_role_policy = lambda **kw: {'PolicyDocument': {'Statement': [{'Effect': 'Allow', 'Action': 'iam:PassRole', 'Resource': '*'}]}}
    if drift == 'role-scope':
        changed = deepcopy(row['role_policies']['scope'])
        changed['Statement'][0]['Resource'] += '/different-route'
        iam.get_role_policy = lambda **kw: {'PolicyDocument': changed}
    with pytest.raises(Denied): verify_runtime(control, iam, row, CALLER)
