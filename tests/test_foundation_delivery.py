"""Offline package, deployment, transport, trace and infrastructure safety."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from botocore.credentials import Credentials
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from foundation_harness.config import digest
from foundation_harness.telemetry import Telemetry
from foundation_harness.transport import IAMTransport, GatewayError
from infra.foundation import template
from scripts.foundation_cloud import inspect_change_set
from scripts.foundation_target import StudioTarget, STACK, PROJECT, REGION, DOMAIN, sanitized
from scripts.package_foundation import package, save_config, source_digest
from backend.foundation_deployment import FoundationDeployment
from backend.runtime_deployment import DeploymentPolicy
from runtime.custom_foundation.main import invoke
from tests.test_foundation_executor import config
from tests.test_runtime_deployment import Control, ACCOUNT, ROLE


def test_exact_bytes_signed_and_transmitted_without_auth_overrides(monkeypatch):
    received = {}
    class Signer:
        def __init__(self, credentials, service, region):
            assert service == 'bedrock-agentcore' and region == 'us-west-2'
        def add_auth(self, request):
            received['signed'] = request.data
    async def send(url, data, headers, timeout):
        received['sent'] = data
        return {}, {}
    monkeypatch.setattr('foundation_harness.transport.SigV4Auth', Signer)
    transport = IAMTransport(SimpleNamespace(get_credentials=lambda: Credentials('synthetic', 'synthetic')))
    monkeypatch.setattr(transport, '_send', send)
    transport.post(config()['model']['endpoint'], {'text': 'Unicode café'}, {'Accept': 'application/json'}, 1)
    assert received['signed'] == received['sent'] == b'{"text":"Unicode caf\xc3\xa9"}'
    for header in ('Authorization', 'X-Amz-Bedrock-AgentCore-Identity-WAT', 'Cookie'):
        with pytest.raises(GatewayError):
            transport.post(config()['model']['endpoint'], {}, {header: 'forged'}, 1)


def test_local_otel_never_records_content_or_exceptions(monkeypatch):
    monkeypatch.setenv('OTEL_RESOURCE_ATTRIBUTES', 'prompt=SENSITIVE_ENV')
    exporter = InMemorySpanExporter()
    telemetry = Telemetry(exporter)
    with pytest.raises(RuntimeError):
        with telemetry.span('run', {'manifest_digest': 'a' * 64}):
            raise RuntimeError('SENSITIVE_PROMPT')
    spans = exporter.get_finished_spans()
    assert len(spans) == 1 and spans[0].events == ()
    assert 'SENSITIVE' not in str(spans[0].attributes)
    assert 'SENSITIVE' not in str(spans[0].resource.attributes)
    with pytest.raises(ValueError, match='TELEMETRY_FIELD'):
        with telemetry.span('model', {'prompt': 'SENSITIVE_PROMPT'}):
            pass
    assert telemetry.trace_id is not None  # SDK-generated LOCAL ID, not live proof.


def test_save_before_runtime_and_deterministic_source_package(tmp_path):
    raw = config()
    raw['foundation']['digest'] = source_digest()
    saved = save_config(raw, tmp_path / 'manifests')
    assert save_config(raw, tmp_path / 'manifests') == saved
    assert saved.stat().st_mode & 0o777 == 0o600
    first = package(saved, tmp_path / 'first.zip')
    second = package(saved, tmp_path / 'second.zip')
    assert first == second and first['production_ready'] is False
    assert (tmp_path / 'first.zip').stat().st_mode & 0o777 == 0o600
    import zipfile
    with zipfile.ZipFile(tmp_path / 'first.zip') as z:
        assert not any('web_research' in name or 'authority' in name for name in z.namelist())
        assert json.loads(z.read('runtime/custom_foundation/harness.json')) == raw
    raw['systemPrompt'][0]['text'] = 'Another domain, same executable source'
    another = save_config(raw, tmp_path / 'manifests')
    assert another != saved
    assert package(another, tmp_path / 'third.zip')['source_digest'] == first['source_digest']


def test_new_runtime_idempotency_and_exact_manifest_vpc(tmp_path):
    raw = config()
    raw['foundation']['digest'] = source_digest()
    saved = save_config(raw, tmp_path)
    network = {'networkMode': 'VPC', 'networkModeConfig': {'subnets': ['subnet-synthetic'], 'securityGroups': ['sg-synthetic']}}
    control = Control()
    adapter = FoundationDeployment(control,
        DeploymentPolicy(REGION, ACCOUNT, frozenset([ROLE]), 'synthetic-artifact-bucket', allow_mutations=True), network)
    args = dict(role=ROLE, artifact_key='proof/package.zip', artifact_version='synthetic-version',
                artifact_manifest_digest=saved.stem, artifact_source_digest=source_digest(),
                network=network, deployment_key='synthetic-deploy')
    adapter.submit_new(saved, **args)
    adapter.submit_new(saved, **args)
    assert control.calls[0] == control.calls[1]
    assert control.calls[0][0] == 'CreateAgentRuntime'
    for delta in ({'network': {'networkMode': 'PUBLIC'}},
                  {'network': {'networkMode': 'VPC', 'networkModeConfig': {}}},
                  {'artifact_manifest_digest': 'b' * 64}):
        with pytest.raises(ValueError):
            adapter.submit_new(saved, **{**args, **delta})
    assert len(control.calls) == 2


def test_production_entry_never_accepts_payload_authority():
    for payload in ({'run_ref': 'opaque'}, {'run_ref': 'opaque', 'owner': 'forged'},
                    {'run_ref': 'opaque', 'manifest': {}}, {'run_ref': 'opaque', 'live': True}):
        assert invoke(payload)['production_ready'] is False
        assert invoke(payload)['status'] in ('BLOCKED', 'DENIED')


def changes(body):
    return {'Status': 'CREATE_COMPLETE', 'ExecutionStatus': 'AVAILABLE', 'StackName': STACK,
            'Changes': [{'ResourceChange': {'Action': 'Add', 'LogicalResourceId': key, 'ResourceType': v['Type']}}
                        for key, v in body['Resources'].items()]}


@pytest.mark.parametrize('tamper', ['remove', 'modify', 'identity', 'stack', 'pagination', 'empty'])
def test_changeset_rejects_outside_scope(tamper):
    body = template()
    response = changes(body)
    if tamper == 'remove': response['Changes'][0]['ResourceChange']['Action'] = 'Remove'
    if tamper == 'modify': response['Changes'][0]['ResourceChange']['Action'] = 'Modify'
    if tamper == 'identity': response['Changes'][0]['ResourceChange']['ResourceType'] = 'AWS::Cognito::UserPool'
    if tamper == 'stack': response['StackName'] = 'shared'
    if tamper == 'pagination': response['NextToken'] = 'more'
    if tamper == 'empty': response['Changes'] = []
    with pytest.raises(RuntimeError):
        inspect_change_set(response, body)


def test_stack_is_separate_enforce_no_admin_no_identity_or_network():
    body = template()
    resources = body['Resources']
    assert len(inspect_change_set(changes(body), body)) == len(resources)
    assert sum(r['Type'] == 'AWS::BedrockAgentCore::Gateway' for r in resources.values()) == 2
    for name in ('Tools', 'Model'):
        g = resources[name + 'Gateway']['Properties']
        assert g['PolicyEngineConfiguration']['Mode'] == 'ENFORCE'
        assert g['AuthorizerType'] == 'AWS_IAM'
    assert not any('Cognito' in r['Type'] or 'EC2' in r['Type'] or 'Runtime' in r['Type']
                   for r in resources.values())
    for r in resources.values():
        if r['Type'] == 'AWS::IAM::Role':
            for p in r['Properties'].get('Policies', []):
                for s in p['PolicyDocument']['Statement']:
                    if s['Effect'] == 'Allow':
                        assert not any(a.startswith('iam:') or a in ('*', 'bedrock:*') for a in s['Action'])
    assert resources['ModelRole']['Properties']['Policies'][0]['PolicyDocument']['Statement'][-1]['Effect'] == 'Deny'
    assert all(resources['Evidence']['Properties']['PublicAccessBlockConfiguration'].values())
    assert resources['Operations']['Properties']['RetentionInDays'] == 7


def test_mantle_catalog_discovery_is_separate_from_inference_and_policy_id():
    resources = template()['Resources']
    statements = resources['ModelRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    assert any(s['Effect'] == 'Allow' and s['Action'] == ['bedrock-mantle:ListModels']
               and s['Resource']['Fn::Sub'].endswith(':project/default') for s in statements)
    assert any(s['Effect'] == 'Deny' and 'bedrock-mantle:CreateInference' in s['Action'] for s in statements)
    # CloudFormation Ref returns the ARN, which exceeds PolicyEngineId's 59-char contract.
    assert resources['ToolPolicy']['Properties']['PolicyEngineId'] == {'Fn::GetAtt': ['PolicyEngine', 'PolicyEngineId']}


def test_repair_only_accepts_reviewed_resources_without_replacement():
    body = template()
    response = changes(body)
    response['Changes'] = [c for c in response['Changes']
                           if c['ResourceChange']['LogicalResourceId'] in ('ModelRole', 'ToolPolicy')]
    for change in response['Changes']:
        change['ResourceChange'].update(Action='Modify', Replacement='False')
    assert len(inspect_change_set(response, body, repair=True)) == 2
    response['Changes'][0]['ResourceChange']['Replacement'] = 'True'
    with pytest.raises(RuntimeError):
        inspect_change_set(response, body, repair=True)
    with pytest.raises(RuntimeError):
        inspect_change_set(changes(body), body, repair=True)
    dependent = {'Status': 'CREATE_COMPLETE', 'ExecutionStatus': 'AVAILABLE', 'StackName': STACK,
        'Changes': [{'ResourceChange': {'LogicalResourceId': 'Evidence', 'Action': 'Modify',
                     'Replacement': 'False', 'ResourceType': 'AWS::S3::Bucket',
                     'Details': [{'Target': {'Attribute': 'Tags', 'RequiresRecreation': 'Never'}}]}}]}
    assert inspect_change_set(dependent, body, repair=True)
    dependent['Changes'][0]['ResourceChange']['Details'][0] = {
        'Target': {'Attribute': 'Properties', 'Name': 'PublicAccessBlockConfiguration', 'RequiresRecreation': 'Never'},
        'ChangeSource': 'DirectModification'}
    with pytest.raises(RuntimeError, match='UNREVIEWED'):
        inspect_change_set(dependent, body, repair=True)


class TargetSDK:
    def __init__(self, mismatch=None):
        self.mismatch = mismatch
        self.calls = []
    def client(self, name, **kwargs):
        self.calls.append(name)
        return self
    def get_caller_identity(self):
        return {'Account': ACCOUNT}
    def describe_stacks(self, **kw):
        account = '0000' + '00000000' if self.mismatch == 'stack' else ACCOUNT
        return {'Stacks': [{'StackId': f'arn:aws:cloudformation:{REGION}:{account}:stack/synthetic/id',
                            'StackStatus': 'CREATE_COMPLETE', 'Tags': [{'Key': 'project', 'Value': PROJECT}]}]}
    def list_stack_resources(self, **kw):
        return {'StackResourceSummaries': [
            {'ResourceType': 'AWS::CloudFront::Distribution', 'PhysicalResourceId': 'synthetic-dist'},
            {'ResourceType': 'AWS::Cognito::UserPool', 'PhysicalResourceId': 'synthetic-pool'}]}
    def get_distribution(self, **kw):
        return {'Distribution': {'DomainName': 'other' if self.mismatch == 'domain' else DOMAIN,
                                 'ARN': f'arn:aws:cloudfront::{ACCOUNT}:distribution/synthetic',
                                 'Status': 'Deployed', 'DistributionConfig': {'Enabled': True}}}
    def describe_user_pool(self, **kw):
        account = '0000' + '00000000' if self.mismatch == 'pool' else ACCOUNT
        return {'UserPool': {'Arn': f'arn:aws:cognito-idp:{REGION}:{account}:userpool/synthetic'}}


@pytest.mark.parametrize('mismatch', ['stack', 'domain', 'pool'])
def test_current_site_gate_mismatch(mismatch):
    sdk = TargetSDK(mismatch)
    with pytest.raises(RuntimeError):
        StudioTarget(sdk).verify()
    assert 'iam' not in sdk.calls


def test_gate_output_and_error_redact_account():
    target = StudioTarget(TargetSDK())
    result = target.verify()
    assert result['target_gate'] == 'MATCH' and ACCOUNT not in json.dumps(result)
    assert ACCOUNT not in sanitized(RuntimeError('AccessDenied: ' + ACCOUNT))


def test_one_attempt_reservation_is_durable_and_unknown_not_zero(tmp_path):
    from scripts.foundation_probe import QUANTITIES, reserve_once
    from decimal import Decimal
    rates = {k: {'source': 'https://aws.amazon.com/bedrock/agentcore/pricing/',
                 'retrieved_at': 'synthetic-test-only', 'applicable': True, 'region': REGION,
                 'usd_per_unit': '0.000001'} for k in QUANTITIES}
    path = tmp_path / 'attempt.json'
    total = reserve_once(path, 'a' * 64, rates)
    assert Decimal(0) < total < Decimal(5)
    assert json.loads(path.read_text())['actual_cost_usd'] is None
    with pytest.raises(FileExistsError):
        reserve_once(path, 'a' * 64, rates)
    with pytest.raises(ValueError, match='COMPLETE_APPLICABLE'):
        reserve_once(tmp_path / 'other.json', 'a' * 64, {})
    rates['gateway_operation']['usd_per_unit'] = '0'
    with pytest.raises(ValueError, match='UNKNOWN_RATE'):
        reserve_once(tmp_path / 'other.json', 'a' * 64, rates)
    assert not (tmp_path / 'other.json').exists()


def test_transport_rejects_redirect_without_following(monkeypatch):
    import httpx
    original = httpx.AsyncClient
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'https://unapproved.example'})
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(reply), **kwargs)
    monkeypatch.setattr('foundation_harness.transport.httpx.AsyncClient', client)
    transport = IAMTransport(None)
    with pytest.raises(GatewayError, match='HTTP_302'):
        asyncio.run(transport._send(config()['model']['endpoint'], b'{}', {}, 1))
    assert len(calls) == 1


def test_otel_exporter_sends_real_protobuf_and_checks_rejection():
    from decimal import Decimal
    from foundation_harness.budget import Budget
    from foundation_harness.config import Limits
    from foundation_harness.telemetry import CloudWatchExporter
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    limits = Limits(**{**config()['limits'], 'maxModelCalls': 1})
    exporter = CloudWatchExporter(None, budget=Budget(limits, reservation_usd=Decimal('0.01')))
    bodies = []
    def send(url, data, headers, timeout, service):
        assert url == 'https://xray.us-west-2.amazonaws.com/v1/traces' and service == 'xray'
        assert headers['x-aws-log-group'] == '/governed-agent-builder/foundation-m0'
        assert headers['x-aws-log-stream'] == 'spans'
        message = ExportTraceServiceRequest()
        message.ParseFromString(data)
        assert message.resource_spans[0].scope_spans[0].spans[0].name == 'run'
        bodies.append(data)
        return None, {}
    exporter.transport.send = send
    telemetry = Telemetry(exporter)
    with telemetry.span('run', {'status': 'SUCCEEDED'}):
        pass
    assert telemetry.flush() and len(bodies) == 1 and exporter.exported == 1
    exporter.transport.send = lambda *a: ({'partialSuccess': {'rejectedSpans': 1}}, {})
    with telemetry.span('run'):
        pass
    assert not telemetry.flush()
